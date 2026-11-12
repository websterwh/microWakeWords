"""Native HA update entity with explicit publisher vs deployed status."""
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TrainerModelUpdate(hass.data[DOMAIN][entry.entry_id], entry)])


class TrainerModelUpdate(CoordinatorEntity, UpdateEntity):
    _attr_has_entity_name = True
    _attr_name = "Wake word model"
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_model"
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
            return "not published" if self.coordinator.adapter == "ha_catalog" else "not deployed"
        return digest[:12]

    @property
    def latest_version(self):
        data = self.coordinator.data
        return data["hash"][:12] if data else None

    @property
    def extra_state_attributes(self):
        if self.coordinator.adapter == "ha_catalog":
            return {
                "delivery": "Home Assistant custom_wake_words",
                "publication_only": True,
                "next_step": "Restart Home Assistant, then select this wake word on the Assist satellite",
                "device_update_confirmed": False,
            }
        return {"delivery": "ESPHome runtime update", "device_update_confirmed": bool(self.coordinator.deployed_hash)}

    @property
    def release_summary(self):
        data = self.coordinator.data
        if not data:
            return None
        label = data["model"].get("label", data["model"].get("key", "unknown"))
        if self.coordinator.adapter == "ha_catalog":
            return f"{label}: publish to Home Assistant; restart and select on device required"
        return f"Trained model: {label}"

    async def async_install(self, version=None, backup=False, **kwargs):
        await self.coordinator.async_install()
