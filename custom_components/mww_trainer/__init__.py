"""Local microWakeWord trainer integration.

Only explicitly configured training targets are sent to the satellite. A
missing trainer response clears the allowlist rather than opening it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from urllib.parse import quote

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, CONF_URL, CONF_MODEL, CONF_BUTTON, CONF_STATUS, CONF_COUNT,
    CONF_TARGETS, CONF_MANIFEST, CONF_EXTRA_TARGETS, CONF_AUTO,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["update", "switch"]


class TrainerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(hass, _LOGGER, name="microWakeWord Trainer",
                         update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.entry = entry
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self.deployed_hash = None
        self.automatic = False
        self._install_lock = asyncio.Lock()
        self._last_targets = None
        self._last_manifest = None
        self._initialized = False

    @property
    def settings(self):
        return {**self.entry.data, **self.entry.options}

    async def async_initialize(self):
        saved = await self.store.async_load() or {}
        self.deployed_hash = saved.get("deployed_hash")
        self.automatic = saved.get("automatic", self.settings.get(CONF_AUTO, False))
        self._initialized = True

    async def save(self):
        await self.store.async_save({"deployed_hash": self.deployed_hash, "automatic": self.automatic})

    async def _get_json(self, path):
        url = self.settings[CONF_URL].rstrip("/") + path
        async with async_get_clientsession(self.hass).get(url, timeout=20) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _async_update_data(self):
        try:
            catalog, auto = await asyncio.gather(
                self._get_json("/api/trained_wake_words/catalog"),
                self._get_json("/api/auto_train"),
            )
            if not isinstance(catalog, dict) or not catalog.get("ok"):
                raise UpdateFailed("Invalid trainer catalog")
            rows = catalog.get("wake_words")
            if not isinstance(rows, list):
                raise UpdateFailed("Trainer catalog has no model list")
            key = self.settings[CONF_MODEL].strip()
            model = next((m for m in rows if isinstance(m, dict) and m.get("key") == key), None)
            if model is None:
                raise UpdateFailed(f"Model {key!r} not present in trainer catalog")
            manifest = model.get("esphome_json_url")
            if not manifest:
                raise UpdateFailed("Selected model has no ESPHome manifest")
            # Never trust catalog-advertised remote hosts for the local fetch;
            # the configured trainer URL is the network trust boundary.
            manifest = self.settings[CONF_URL].rstrip("/") + "/api/trained_wake_words/" + quote(key + ".esphome.json")
            model_url = self.settings[CONF_URL].rstrip("/") + "/api/trained_wake_words/" + quote(key + ".tflite")
            async with async_get_clientsession(self.hass).get(model_url, timeout=90) as response:
                response.raise_for_status()
                digest = hashlib.sha256(await response.read()).hexdigest()

            config = auto.get("config", {}) if isinstance(auto, dict) else {}
            phrase = str(config.get("wake_phrase") or "").strip()
            # Secondary wake words may be specified explicitly. Never infer
            # training targets from all compiled firmware models.
            extra = [word.strip() for word in self.settings.get(CONF_EXTRA_TARGETS, "").split(",") if word.strip()]
            targets = list(dict.fromkeys(([phrase] if phrase else []) + extra))
            if not targets:
                raise UpdateFailed("No active training target is configured")
            # Synchronize the allowlist and destination BEFORE allowing uploads.
            await self._set_text(CONF_TARGETS, ",".join(targets), "_last_targets")
            await self._set_text(CONF_MANIFEST, manifest, "_last_manifest")
            return {"hash": digest, "manifest": manifest, "targets": targets, "model": model}
        except (ClientError, asyncio.TimeoutError, ValueError, KeyError, UpdateFailed) as err:
            # Stop captures when the trainer is unreachable or no longer has
            # an explicit training target. Wake-word recognition is unaffected.
            try:
                await self._set_text(CONF_TARGETS, "", "_last_targets")
            except Exception:
                _LOGGER.warning("Could not clear trainer capture targets after refresh failure")
            raise UpdateFailed(f"Unable to retrieve trainer model: {err}") from err

    async def _set_text(self, key, value, cache_attr):
        entity_id = self.settings[key]
        current = self.hass.states.get(entity_id)
        if current is not None and current.state == value:
            setattr(self, cache_attr, value)
            return
        if getattr(self, cache_attr) == value and current is not None:
            return
        await self.hass.services.async_call("text", "set_value",
             {"entity_id": entity_id, "value": value}, blocking=True)
        setattr(self, cache_attr, value)

    async def async_install(self):
        if not self.last_update_success or not self.data:
            raise HomeAssistantError("Trainer model is unavailable")
        async with self._install_lock:
            target_hash = self.data["hash"]
            initial_count = self._count()
            if initial_count is None:
                raise HomeAssistantError("Update count sensor is unavailable")
            await self._set_text(CONF_MANIFEST, self.data["manifest"], "_last_manifest")
            await self.hass.services.async_call("button", "press",
                {"entity_id": self.settings[CONF_BUTTON]}, blocking=True)
            # Count + success are both required. A failed update never marks a
            # hash deployed, and we do not infer success from the button press.
            for _ in range(120):
                await asyncio.sleep(1)
                count = self._count()
                status = self.hass.states.get(self.settings[CONF_STATUS])
                if status is not None and status.state.lower() == "failed":
                    raise HomeAssistantError("Device reported model update failure")
                if count is not None and count > initial_count and status is not None and status.state.lower() == "success":
                    self.deployed_hash = target_hash
                    await self.save()
                    self.async_update_listeners()
                    return
            raise HomeAssistantError("Timed out waiting for confirmed model installation")

    def _count(self):
        state = self.hass.states.get(self.settings[CONF_COUNT])
        if state is None:
            return None
        try:
            return int(float(state.state))
        except (ValueError, TypeError):
            return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = TrainerCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def check_auto():
        if (coordinator.automatic and coordinator.last_update_success
                and coordinator.deployed_hash != coordinator.data["hash"]
                and not coordinator._install_lock.locked()):
            try:
                await coordinator.async_install()
            except HomeAssistantError as err:
                _LOGGER.warning("Automatic wake-word deployment failed: %s", err)

    # Refresh on the normal coordinator schedule, deploy after a successful refresh.
    entry.async_on_unload(coordinator.async_add_listener(lambda: hass.async_create_task(check_auto())))
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
