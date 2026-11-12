"""Choose automatic or approval-based wake-word deployment."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TrainerAutomaticUpdates(hass.data[DOMAIN][entry.entry_id], entry)])


class TrainerAutomaticUpdates(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Automatic model updates"
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinators, entry):
        super().__init__(coordinators[0])
        self.coordinators = coordinators
        self._attr_unique_id = f"{entry.entry_id}_auto_update"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "microWakeWord Trainer",
            "manufacturer": "TaterTotterson",
            "model": "Local microWakeWord Trainer",
        }

    @property
    def is_on(self):
        return bool(self.coordinators) and all(coordinator.automatic for coordinator in self.coordinators)

    async def async_turn_on(self, **kwargs):
        for coordinator in self.coordinators:
            coordinator.automatic = True
            await coordinator.save()
        self.async_write_ha_state()
        for coordinator in self.coordinators:
            await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        for coordinator in self.coordinators:
            coordinator.automatic = False
            await coordinator.save()
        self.async_write_ha_state()
