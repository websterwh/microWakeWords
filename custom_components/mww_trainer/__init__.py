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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalog import publish
from .const import (
    DOMAIN, CONF_URL, CONF_MODEL, CONF_BUTTON, CONF_STATUS,
    CONF_COUNT, CONF_TARGETS, CONF_MANIFEST, CONF_EXTRA_TARGETS,
    CONF_AUTO, CONF_DEVICE, SCAN_INTERVAL_SECONDS,
)
from .device import device_adapter, configured_entities, UnsupportedDevice

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["update", "switch"]


class TrainerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_id=None):
        super().__init__(
            hass, _LOGGER, name="microWakeWord Trainer",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.device_id = device_id
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_{device_id or 'legacy'}")
        self.deployed_hash = None
        self.published_hash = None
        self.automatic = False
        self._install_lock = asyncio.Lock()

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
        return self.deployed_hash if self.adapter == "esphome" else self.published_hash

    async def async_initialize(self):
        saved = await self.store.async_load() or {}
        self.deployed_hash = saved.get("deployed_hash")
        self.published_hash = saved.get("published_hash")
        self.automatic = saved.get("automatic", self.settings.get(CONF_AUTO, False))

    async def save(self):
        await self.store.async_save({
            "deployed_hash": self.deployed_hash,
            "published_hash": self.published_hash,
            "automatic": self.automatic,
        })

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
            model = await response.content.read(1048577)
            if not model or len(model) > 1048576:
                raise UpdateFailed("Trained wake-word model is empty or exceeds 1 MiB")
            return model

    async def _async_update_data(self):
        try:
            mode = self.adapter
            catalog, auto = await asyncio.gather(
                self._get_json("/api/trained_wake_words/catalog"),
                self._get_json("/api/auto_train"),
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
            manifest_url = self._url(manifest_path)
            config = auto.get("config", {}) if isinstance(auto, dict) else {}
            phrase = str(config.get("wake_phrase") or "").strip()
            extras = [x.strip() for x in self.settings.get(CONF_EXTRA_TARGETS, "").split(",") if x.strip()]
            targets = list(dict.fromkeys(([phrase] if phrase else []) + extras))
            if mode == "esphome":
                # Fail closed: only user-approved training targets are uploaded.
                await self._set_text(CONF_TARGETS, ",".join(targets))
                if manifest_error is None:
                    await self._set_text(CONF_MANIFEST, manifest_url)
            return {
                "hash": digest, "manifest": manifest_url,
                "manifest_json": manifest_json, "model_bytes": model_bytes,
                "model": model, "key": key, "targets": targets, "adapter": mode,
                "manifest_error": manifest_error,
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
        if current is None or current.state in ("unavailable", "unknown"):
            raise HomeAssistantError(f"Required trainer entity is unavailable: {entity_id}")
        if current.state != value:
            await self.hass.services.async_call(
                "text", "set_value",
                {"entity_id": entity_id, "value": value}, blocking=True,
            )

    def _count(self):
        state = self.hass.states.get(configured_entities(self.hass, self.settings)[CONF_COUNT])
        if state is None:
            return None
        try:
            return int(float(state.state))
        except (TypeError, ValueError):
            return None

    async def async_install(self):
        if not self.last_update_success or not self.data:
            raise HomeAssistantError("Trainer model is unavailable")
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
                    self.deployed_hash = target_hash
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
            if (current.automatic and current.last_update_success and
                    current.installed_hash != current.data["hash"] and
                    not current._install_lock.locked()):
                try:
                    await current.async_install()
                except (HomeAssistantError, ValueError) as err:
                    _LOGGER.warning("Automatic model update failed for %s: %s",
                                    current.device_id, err)

        entry.async_on_unload(coordinator.async_add_listener(
            lambda fn=check_auto: hass.async_create_task(fn())
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
