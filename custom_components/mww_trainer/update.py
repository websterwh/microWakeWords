"""One native update entity for each selected wake-word satellite.

A SHA-256 digest is the identity of a trained model, not an ordered version
number. The entity therefore reports full digests (never 12-character prefixes,
which cannot be compared) and decides availability from the shared revision
ledger rather than from AwesomeVersion ordering.
"""
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .revision import abbreviate


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
    def _status(self):
        return self.coordinator.revision_status

    @property
    def installed_version(self):
        """Full digest the satellite reports, or None while unknown.

        HA derives entity state from these two properties: with an unknown
        installed digest the state stays ``unknown`` rather than claiming the
        satellite is up to date.
        """
        return self._status.installed_hash

    @property
    def latest_version(self):
        """Full digest of the trainer's newest published model revision."""
        return self._status.trainer_hash

    @property
    def has_update(self):
        """True only for a revision published after the established baseline."""
        return self._status.has_update

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        status = self._status
        if self.coordinator.adapter == "ha_catalog":
            return {
                "delivery": "Home Assistant custom_wake_words",
                "publication_only": True,
                "next_step": "Restart Home Assistant and choose the new wake word on the satellite",
                "device_update_confirmed": False,
                "trainer_model_hash": status.trainer_hash,
                "trainer_hash_short": abbreviate(status.trainer_hash),
                "published_hash": status.installed_hash,
                "published_hash_short": abbreviate(status.installed_hash),
                "baseline_hash": status.baseline_hash,
                "new_release_available": status.new_release,
                "initial_adoption": status.initial_adoption,
                "trainer_revision": status.trainer_revision,
                "revision_count": status.revision_count,
                "auto_update_attempts": self.coordinator.auto_attempts.get(
                    status.trainer_hash or "", 0
                ),
            }
        no_hash_sensor = status.installed_hash is None
        attributes = {
            "delivery": "ESPHome runtime update",
            "trainer_model_hash": status.trainer_hash,
            "installed_version_short": abbreviate(status.installed_hash),
            "latest_version_short": abbreviate(status.trainer_hash),
            "baseline_hash": status.baseline_hash,
            "baseline_hash_short": abbreviate(status.baseline_hash),
            "new_release_available": status.new_release,
            "initial_adoption": status.initial_adoption,
            "device_model_matches_trainer": status.device_matches_trainer,
            "device_update_confirmed": status.device_matches_trainer is True,
            "installed_hash_verified": not no_hash_sensor,
            "trainer_revision": status.trainer_revision,
            "revision_count": status.revision_count,
            "manual_deploy_available": status.trainer_hash is not None,
            "auto_update_attempts": self.coordinator.auto_attempts.get(
                status.trainer_hash or "", 0
            ),
            "trainer_manifest_error": data.get("manifest_error"),
        }
        if no_hash_sensor:
            attributes["installed_version_note"] = (
                "Firmware does not report a Runtime Model SHA256, so the installed "
                "model is unknown. Reflash with the latest mww_trainer.yaml package "
                "to enable installed-model tracking; automatic updates stay off "
                "until the device reports a verified model."
            )
        return attributes

    @property
    def release_summary(self):
        data = self.coordinator.data
        if not data:
            return None
        status = self._status
        label = data["model"].get("label") or data["model"].get("key") or "unknown"
        if self.coordinator.adapter == "ha_catalog":
            return f"{label}: publish to Home Assistant; restart and select on device"
        if data.get("manifest_error"):
            return f"{label}: trainer manifest unavailable; check trainer server logs"
        summary = f"Trained model: {label} · model {abbreviate(status.trainer_hash)}"
        if status.trainer_revision:
            summary += f" · trained {status.trainer_revision}"
        if status.new_release:
            summary += " · new revision"
        elif status.up_to_date:
            summary += " · installed and verified"
        return summary

    async def async_install(self, version=None, backup=False, **kwargs):
        await self.coordinator.async_install()
