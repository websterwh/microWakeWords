"""One native update entity for each selected wake-word satellite."""
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        TrainerModelUpdate(coordinator, entry)
        for coordinator in hass.data[DOMAIN][entry.entry_id]
    ])


class TrainerModelUpdate(CoordinatorEntity, UpdateEntity):
    _attr_has_entity_name = True
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        device = (
            dr.async_get(coordinator.hass).async_get(coordinator.device_id)
            if coordinator.device_id else None
        )
        name = (device.name_by_user or device.name) if device else "Wake-word device"
        self._attr_name = f"{name} model"
        self._attr_unique_id = f"{entry.entry_id}_{coordinator.device_id or 'legacy'}_model"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "microWakeWord Trainer",
            "manufacturer": "TaterTotterson",
            "model": "Local microWakeWord Trainer",
        }

    @property
    def installed_version(self):
        digest = self.coordinator.installed_hash
        if not digest:
            # Unknown is not proof an update is required.
            return None
        return digest[:12]

    @property
    def latest_version(self):
        data = self.coordinator.data
        if not data:
            return None
        # Without an installed-model fingerprint, do not emit an unverified
        # "Update available" notice immediately after integration setup.
        if self.coordinator.adapter == "esphome" and self.coordinator.installed_hash is None:
            return None
        return data["hash"][:12]

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.coordinator.adapter == "ha_catalog":
            return {
                "delivery": "Home Assistant custom_wake_words",
                "publication_only": True,
                "next_step": "Restart Home Assistant and choose the new wake word on the satellite",
                "device_update_confirmed": False,
            }
        no_hash_sensor = self.coordinator.installed_hash is None
        return {
            "delivery": "ESPHome runtime update",
            "device_update_confirmed": not no_hash_sensor,
            "installed_hash_verified": not no_hash_sensor,
            "trainer_manifest_error": data.get("manifest_error"),
            **({
                "installed_version_note": (
                    "Firmware does not yet expose a Runtime Model SHA256 sensor. "
                    "Reflash with the latest voice_pe_trainer.yaml package to enable "
                    "installed-version tracking."
                )
            } if no_hash_sensor else {}),
        }

    @property
    def release_summary(self):
        data = self.coordinator.data
        if not data:
            return None
        label = data["model"].get("label", data["model"].get("key", "unknown"))
        if self.coordinator.adapter == "ha_catalog":
            return f"{label}: publish to Home Assistant; restart and select on device"
        if data.get("manifest_error"):
            return f"{label}: trainer manifest unavailable; check trainer server logs"
        return f"Trained model: {label}"

    async def async_install(self, version=None, backup=False, **kwargs):
        await self.coordinator.async_install()
