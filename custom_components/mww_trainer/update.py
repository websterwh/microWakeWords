"""Native Home Assistant update entity for the trained model."""
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
        value = self.coordinator.deployed_hash
        return value[:12] if value else "not deployed"

    @property
    def latest_version(self):
        data = self.coordinator.data
        return data["hash"][:12] if data else None

    @property
    def release_summary(self):
        data = self.coordinator.data
        if not data:
            return None
        return f"Trained model: {data['model'].get('label', data['model'].get('key', 'unknown'))}"

    async def async_install(self, version=None, backup=False, **kwargs):
        await self.coordinator.async_install()
