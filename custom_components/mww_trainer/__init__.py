"""Local microWakeWord trainer integration with device-capability adapters."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from urllib.parse import quote

from aiohttp import ClientError, ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalog import publish
from .const import (
    DOMAIN, CONF_URL, CONF_MODEL, CONF_BUTTON, CONF_STATUS,
    CONF_COUNT, CONF_TARGETS, CONF_MANIFEST, CONF_EXTRA_TARGETS,
    CONF_AUTO, CONF_DEVICE, SCAN_INTERVAL_SECONDS,
)
from .device import device_adapter, configured_entities, UnsupportedDevice
from .revision import (
    MAX_AUTO_ATTEMPTS, RevisionStatus, RevisionTracker,
    may_auto_install, normalise_hash, verified_deployment,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["update", "switch"]


def revision_group(hass: HomeAssistant, settings):
    """Return the revision ledger shared by every satellite of a trainer/model.

    Two config entries (or two satellites of one entry) must agree on what a
    "new" revision is, so the ledger is keyed by trainer origin and model, not
    by config entry. Device installation status stays per satellite.
    """
    registry = hass.data.setdefault(f"{DOMAIN}_revisions", {})
    identity = "{}|{}".format(
        str(settings.get(CONF_URL) or "").strip().rstrip("/").casefold(),
        str(settings.get(CONF_MODEL) or "").strip().casefold(),
    )
    key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    group = registry.get(key)
    if group is None:
        group = {
            "store": Store(hass, 1, f"{DOMAIN}_revision_{key}"),
            "tracker": None,
            "lock": asyncio.Lock(),
        }
        registry[key] = group
    return group


class TrainerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_id=None):
        super().__init__(
            hass, _LOGGER, name="microWakeWord Trainer",
            # Let the coordinator own the polling cadence. It schedules the next
            # refresh after every completed refresh while listeners exist (the
            # entities attach listeners during platform setup), so an external
            # timer is unnecessary and can silently stop firing.
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.device_id = device_id
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_{device_id or 'legacy'}")
        self.deployed_hash = None
        self.published_hash = None
        self.automatic = False
        self.auto_attempts = {}
        self._install_lock = asyncio.Lock()
        self._revision_group = revision_group(hass, self.settings)

    @property
    def settings(self):
        settings = {**self.entry.data, **self.entry.options}
        if self.device_id:
            settings[CONF_DEVICE] = self.device_id
        return settings

    @property
    def adapter(self):
        if not self.settings.get(CONF_DEVICE):
            # Backwards compatibility for old integrations storing five entities.
            return "esphome"
        return device_adapter(self.hass, self.settings[CONF_DEVICE])[0]

    @property
    def installed_hash(self):
        if self.adapter == "ha_catalog":
            return self.published_hash
        # Trust what the firmware reports, NOT the integration's saved history.
        # Older firmware has no hash sensor: unknown is not "update available".
        entities = configured_entities(self.hass, self.settings)
        entity_id = entities.get("runtime_hash_entity")
        state = self.hass.states.get(entity_id) if entity_id else None
        # Full 64-character digest only. Abbreviations are display-only, and
        # "unknown" means the compiled fallback is running, not a model.
        return normalise_hash(state.state if state else None)

    @property
    def revision_status(self) -> RevisionStatus:
        """Trainer revision availability as seen by this satellite."""
        group = self._revision_group
        tracker = group.get("tracker") if group else None
        if tracker is None:
            return RevisionStatus()
        return tracker.status(installed_hash=self.installed_hash)

    async def async_initialize(self):
        saved = await self.store.async_load() or {}
        self.deployed_hash = saved.get("deployed_hash")
        self.published_hash = saved.get("published_hash")
        self.automatic = saved.get("automatic", self.settings.get(CONF_AUTO, False))
        attempts = saved.get("auto_attempts")
        self.auto_attempts = dict(attempts) if isinstance(attempts, dict) else {}

    async def save(self):
        await self.store.async_save({
            "deployed_hash": self.deployed_hash,
            "published_hash": self.published_hash,
            "automatic": self.automatic,
            "auto_attempts": self.auto_attempts,
        })

    async def _async_observe_revision(self, digest, model):
        """Record the published trainer revision in the shared ledger."""
        group = self._revision_group
        async with group["lock"]:
            if group["tracker"] is None:
                group["tracker"] = RevisionTracker.from_dict(
                    await group["store"].async_load()
                )
            tracker = group["tracker"]
            before = tracker.to_dict()
            # The published artifact's own timestamp is authoritative. The
            # trainer's latest_run is omitted: it can describe a failed run that
            # published nothing (observed live), so it cannot gate a release.
            status = tracker.observe(
                digest,
                revision=model.get("calibration_generated_at") or model.get("published_at"),
            )
            if tracker.to_dict() != before:
                await group["store"].async_save(tracker.to_dict())
                _LOGGER.info(
                    "Trainer revision %s observed for device %s (baseline %s, "
                    "new release: %s, revisions seen: %s)",
                    digest[:12], self.device_id, (status.baseline_hash or "")[:12],
                    status.new_release, status.revision_count,
                )
            seen = set(tracker.seen_revisions)
            for attempt_hash in [h for h in self.auto_attempts if h not in seen]:
                self.auto_attempts.pop(attempt_hash, None)
            return status

    def _url(self, path):
        return self.settings[CONF_URL].rstrip("/") + path

    async def _get_json(self, path):
        async with async_get_clientsession(self.hass).get(
            self._url(path), timeout=ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _get_model(self, path):
        async with async_get_clientsession(self.hass).get(
            self._url(path), timeout=ClientTimeout(total=90)
        ) as response:
            response.raise_for_status()
            # Do not load an arbitrarily large file into HA's memory.
            if response.content_length is not None and response.content_length > 1048576:
                raise UpdateFailed("Trained wake-word model exceeds 1 MiB")
            # StreamReader.read(n) returns as soon as *some* bytes have arrived,
            # so a single read can yield a truncated prefix of the model. Hashing
            # that prefix invents revisions that do not exist. Read to EOF.
            payload = bytearray()
            while True:
                block = await response.content.read(65536)
                if not block:
                    break
                payload.extend(block)
                if len(payload) > 1048576:
                    raise UpdateFailed("Trained wake-word model is empty or exceeds 1 MiB")
            if not payload:
                raise UpdateFailed("Trained wake-word model is empty or exceeds 1 MiB")
            # A short body means the transfer was cut; never hash a partial model.
            if (response.content_length is not None
                    and "content-encoding" not in response.headers
                    and len(payload) != response.content_length):
                raise UpdateFailed(
                    "Trained wake-word model download was incomplete "
                    f"({len(payload)} of {response.content_length} bytes)"
                )
            return bytes(payload)

    async def _async_update_data(self):
        try:
            mode = self.adapter
            catalog, library = await asyncio.gather(
                self._get_json("/api/trained_wake_words/catalog"),
                self._get_json("/api/wake_word_library"),
            )
            if not isinstance(catalog, dict) or not catalog.get("ok"):
                raise UpdateFailed("Invalid trainer catalog")
            rows = catalog.get("wake_words")
            if not isinstance(rows, list):
                raise UpdateFailed("Trainer catalog has no model list")
            requested = self.settings[CONF_MODEL].strip().casefold()
            model = next((
                row for row in rows if isinstance(row, dict)
                and any(str(row.get(field) or "").casefold() == requested
                        for field in ("key", "label", "wake_word", "wake_word_name"))
            ), None)
            if model is None:
                raise UpdateFailed(f"Wake word/model {requested!r} not present in trainer catalog")
            key = str(model.get("key") or "")
            if not key or not all(ch.isalnum() or ch in "_-" for ch in key):
                raise UpdateFailed("Unsafe model key returned by trainer")
            if not model.get("esphome_json_url"):
                raise UpdateFailed("Selected model has no ESPHome manifest")
            # Construct URLs using the trusted configured trainer origin rather
            # than following catalog-advertised URLs to arbitrary hosts.
            manifest_path = "/api/trained_wake_words/" + quote(key + ".esphome.json")
            model_file = str(model.get("model_file") or (key + ".tflite"))
            if "/" in model_file or "\\" in model_file or not model_file.endswith(".tflite"):
                raise UpdateFailed("Unsafe model filename returned by trainer")
            model_path = "/api/trained_wake_words/" + quote(model_file)
            model_bytes = await self._get_model(model_path)
            manifest_json = None
            manifest_error = None
            try:
                manifest_json = await self._get_json(manifest_path)
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                manifest_error = str(err)
                _LOGGER.warning("Trainer ESPHome manifest unavailable for %s: %s", key, err)
                # The published wake bundle is an independent source for HA catalog metadata.
                try:
                    bundle = await self._get_json(
                        "/api/trained_wake_words/" + quote(key + ".wake-bundle.json")
                    )
                    meta = bundle.get("micro_wake_word", {}) if isinstance(bundle, dict) else {}
                    if isinstance(meta, dict):
                        manifest_json = {
                            field: meta[field] for field in (
                                "type", "wake_word", "author", "website", "model",
                                "trained_languages", "version", "micro"
                            ) if field in meta
                        }
                except (ClientError, asyncio.TimeoutError, ValueError) as fallback_err:
                    _LOGGER.warning("Trainer wake bundle fallback unavailable: %s", fallback_err)
            if not isinstance(manifest_json, dict) or manifest_json.get("type") != "micro":
                manifest_json = None
            digest = hashlib.sha256(model_bytes).hexdigest()
            await self._async_observe_revision(digest, model)
            manifest_url = self._url(manifest_path)
            # The trainer library's active selection is the training target.
            # Auto-training's wake_phrase can be unset when training manually,
            # and must not override the library's active word.
            if not isinstance(library, dict) or not library.get("ok") or not isinstance(library.get("words"), list):
                raise UpdateFailed("Invalid trainer wake-word library response")
            active = [
                str(word.get("phrase") or "").strip()
                for word in library["words"]
                if isinstance(word, dict) and word.get("active")
            ]
            extras = [x.strip() for x in self.settings.get(CONF_EXTRA_TARGETS, "").split(",") if x.strip()]
            targets = list(dict.fromkeys(x for x in (active + extras) if x))
            device_ready = True
            if mode == "esphome":
                # An ESPHome text entity is often "unavailable" during HA
                # startup or satellite reconnection. Do not make trainer
                # discovery dependent on device availability. The firmware
                # upload allowlist defaults to empty until synchronized.
                try:
                    # The URL is useful even when the trainer endpoint is
                    # temporarily returning HTTP 500. Keep it synchronized.
                    await self._set_text(CONF_MANIFEST, manifest_url)
                    await self._set_text(CONF_TARGETS, ",".join(targets))
                    # Also synchronize the uploader base URL if supported.
                    if configured_entities(self.hass, self.settings).get("trainer_url_entity"):
                        await self._set_text("trainer_url_entity", self.settings[CONF_URL])
                except HomeAssistantError as err:
                    device_ready = False
                    _LOGGER.info("Wake-word satellite %s not ready for synchronization: %s", self.device_id, err)
            return {
                "hash": digest, "manifest": manifest_url,
                "manifest_json": manifest_json, "model_bytes": model_bytes,
                "model": model, "key": key, "targets": targets, "adapter": mode,
                "manifest_error": manifest_error, "device_ready": device_ready,
            }
        except (ClientError, asyncio.TimeoutError, ValueError, KeyError, UpdateFailed,
                UnsupportedDevice) as err:
            # When the trainer goes unavailable, do not leave a stale target
            # allowlist active on ESPHome's direct upload implementation.
            try:
                if self.adapter == "esphome":
                    await self._set_text(CONF_TARGETS, "")
            except (HomeAssistantError, UnsupportedDevice, ValueError) as clear_err:
                _LOGGER.warning("Could not disable wake word capture after trainer error: %s", clear_err)
            raise UpdateFailed("Unable to refresh trainer: %s" % err) from err

    async def _set_text(self, role, value):
        entity_id = configured_entities(self.hass, self.settings)[role]
        current = self.hass.states.get(entity_id)
        if current is None or current.state == "unavailable":
            raise HomeAssistantError(f"Required trainer entity is unavailable: {entity_id}")
        # ESPHome template text inputs can initially report "unknown" even
        # while the device is connected. This is an unset *writable* value,
        # not a reason to reject synchronization: send text.set_value to
        # initialize it. ESPHome optimistic text publishes the new state.
        if current.state == "unknown" or current.state != value:
            _LOGGER.debug(
                "Setting %s = %r (was %r) for device %s",
                entity_id, value, current.state, self.device_id,
            )
            await self.hass.services.async_call(
                "text", "set_value",
                {"entity_id": entity_id, "value": value}, blocking=True,
            )
        else:
            _LOGGER.debug(
                "Skipping %s — already %r for device %s",
                entity_id, value, self.device_id,
            )

    def _count(self):
        state = self.hass.states.get(configured_entities(self.hass, self.settings)[CONF_COUNT])
        if state is None:
            return None
        try:
            return int(float(state.state))
        except (TypeError, ValueError):
            return None

    async def _async_confirm_deployment(self, target_hash):
        """Require the satellite's own runtime digest to match the deployed model.

        A successful update status only proves the firmware accepted a file. The
        runtime SHA-256 sensor proves *which* model is actually loaded, so an
        installation is recorded as deployed only on an exact 64-character
        match. Compiled-only firmware reports "unknown" and is never matched.
        """
        if not configured_entities(self.hass, self.settings).get("runtime_hash_entity"):
            # Older firmware has no fingerprint to check. Recording the attempt
            # is harmless: the entity still reports an unknown installed version
            # and automatic deployment stays disabled without a verified hash.
            _LOGGER.warning(
                "Device %s has no Runtime Model SHA256 sensor; the deployment "
                "cannot be verified against the loaded model", self.device_id,
            )
            return target_hash
        for _ in range(60):
            if verified_deployment(target_hash, self.installed_hash) is not None:
                return target_hash
            await asyncio.sleep(1)
        raise HomeAssistantError(
            "Device reported success but its loaded model hash {} does not match "
            "the deployed model {}; the installation was not recorded".format(
                (self.installed_hash or "unknown")[:12], target_hash[:12]
            )
        )

    async def async_install(self):
        if not self.last_update_success or not self.data:
            raise HomeAssistantError("Trainer model is unavailable")
        if self.adapter == "esphome" and not self.data.get("device_ready", False):
            raise HomeAssistantError("Satellite is unavailable; wait for ESPHome reconnect and retry")
        async with self._install_lock:
            target_hash = self.data["hash"]
            if self.adapter == "ha_catalog":
                if self.data["manifest_json"] is None:
                    raise HomeAssistantError("Trainer manifest and wake bundle are unavailable; check trainer logs")
                # This publishes to HA, NOT to the device. HA must restart and
                # the model must be selected on the satellite before it loads.
                await publish(
                    self.hass, self.data["key"], self.data["manifest_json"],
                    self.data["model_bytes"],
                )
                self.published_hash = target_hash
                await self.save()
                self.async_update_listeners()
                return

            if self.data.get("manifest_error"):
                raise HomeAssistantError(
                    "Trainer returned an error for the ESPHome manifest. Check trainer logs: "
                    + self.data["manifest_error"]
                )
            initial_count = self._count()
            if initial_count is None:
                raise HomeAssistantError("Model update counter is unavailable")
            await self._set_text(CONF_MANIFEST, self.data["manifest"])
            await self.hass.services.async_call(
                "button", "press",
                {"entity_id": configured_entities(self.hass, self.settings)[CONF_BUTTON]},
                blocking=True,
            )
            for _ in range(120):
                await asyncio.sleep(1)
                count = self._count()
                status = self.hass.states.get(
                    configured_entities(self.hass, self.settings)[CONF_STATUS]
                )
                if status is not None and status.state.casefold() == "failed":
                    raise HomeAssistantError("Device reported model update failure")
                if (count is not None and count > initial_count and status is not None
                        and status.state.casefold() == "success"):
                    # Never record an installation the device does not confirm.
                    self.deployed_hash = await self._async_confirm_deployment(target_hash)
                    await self.save()
                    self.async_update_listeners()
                    return
            raise HomeAssistantError("Timed out waiting for confirmed model installation")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    selected = {**entry.data, **entry.options}.get(CONF_DEVICE)
    device_ids = selected if isinstance(selected, list) else ([selected] if selected else [None])
    coordinators = []
    for device_id in dict.fromkeys(device_ids):
        coordinator = TrainerCoordinator(hass, entry, device_id)
        await coordinator.async_initialize()
        await coordinator.async_config_entry_first_refresh()
        coordinators.append(coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    for coordinator in coordinators:
        async def check_auto(current=coordinator):
            if not current.last_update_success or not current.data:
                return
            status = current.revision_status
            device_ready = (
                current.adapter != "esphome"
                or bool(current.data.get("device_ready", False))
            )
            attempts = current.auto_attempts.get(status.trainer_hash or "", 0)
            # Requires a revision published after the baseline: the initial
            # adoption baseline is never installed automatically, and an
            # unknown or compiled-only satellite model never qualifies.
            if (current._install_lock.locked() or
                    not may_auto_install(status, automatic=current.automatic,
                                         device_ready=device_ready, attempts=attempts)):
                return
            key = status.trainer_hash
            current.auto_attempts[key] = attempts + 1
            await current.save()
            _LOGGER.info(
                "Automatic model update for %s: %s -> %s (attempt %s of %s)",
                current.device_id, (status.installed_hash or "unknown")[:12],
                key[:12], attempts + 1, MAX_AUTO_ATTEMPTS,
            )
            try:
                await current.async_install()
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("Automatic model update failed for %s: %s",
                                current.device_id, err)

        entry.async_on_unload(coordinator.async_add_listener(
            lambda fn=check_auto: hass.async_create_task(fn())
        ))
        if coordinator.adapter == "esphome":
            # Retry synchronization as soon as the ESPHome satellite reconnects.
            # ESPHome optimistic text entities restore to "unknown" (not
            # "unavailable") when the device reconnects after HA start or a
            # device reboot. The coordinator must write the training targets and
            # manifest URL at that point. We watch two distinct transitions:
            #
            # 1. unavailable -> unknown: ESPHome device just connected and HA
            #    has not yet received any value from the device. This is the
            #    right moment to trigger a coordinator refresh and push values.
            #
            # 2. unavailable/unknown -> any real value: a value already existed
            #    in flash (restore_value: true) and ESPHome published it; we
            #    still refresh so the coordinator can verify / overwrite if
            #    the trainer-side targets have changed.
            #
            # We also watch the runtime_hash_entity so that a model deployment
            # (which changes the hash) immediately updates the update entity.
            entities = configured_entities(hass, coordinator.settings)
            ids = [entities[role] for role in (CONF_TARGETS, CONF_MANIFEST)]
            if entities.get("runtime_hash_entity"):
                ids.append(entities["runtime_hash_entity"])
            async def on_entity_state_change(event, current=coordinator):
                new_state = event.data.get("new_state")
                old_state = event.data.get("old_state")
                old_s = old_state.state if old_state is not None else None
                new_s = new_state.state if new_state is not None else None
                # Trigger on reconnect: unavailable -> unknown (ESPHome just
                # connected, entity has no value yet) or unavailable/unknown ->
                # real value (restore_value fired). Both need a coordinator
                # refresh to push the correct targets + manifest URL.
                if (
                    new_s is not None
                    and old_s == "unavailable"
                    and new_s != "unavailable"
                ):
                    _LOGGER.debug(
                        "ESPHome entity %s came back from unavailable (now %r); "
                        "requesting coordinator refresh for %s",
                        event.data.get("entity_id"), new_s, current.device_id,
                    )
                    await current.async_request_refresh()
                # Fingerprint changes (including a manual model deployment
                # or a device reboot/reflash) should trigger a full coordinator
                # refresh to re-verify the installed model against the trainer.
                # Also trigger on first appearance of the hash sensor (old_s None
                # means this is the first state we've seen for it after HA start
                # or device reconnect).
                if (
                    event.data.get("entity_id")
                    == configured_entities(hass, current.settings).get("runtime_hash_entity")
                    and new_s is not None
                    and (old_s is None or old_s != new_s)
                ):
                    _LOGGER.debug(
                        "Runtime model hash %s for %s (%r -> %r); "
                        "requesting coordinator refresh to verify against trainer",
                        'appeared' if old_s is None else 'changed',
                        current.device_id, old_s, new_s,
                    )
                    await current.async_request_refresh()
            entry.async_on_unload(async_track_state_change_event(
                hass, ids, on_entity_state_change
            ))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        entry.async_unload()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return result
