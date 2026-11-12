"""Discover device capabilities using HA registries, never hardcoded device IDs."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_DEVICE, CONF_BUTTON, CONF_STATUS, CONF_COUNT, CONF_TARGETS, CONF_MANIFEST

REQUIRED = {
    CONF_BUTTON: ("button", ("update", "wake", "word", "model")),
    CONF_STATUS: ("sensor", ("model", "update", "status")),
    CONF_COUNT: ("sensor", ("model", "update", "count")),
    CONF_TARGETS: ("text", ("trainer", "active", "wake", "words")),
    CONF_MANIFEST: ("text", ("trainer", "model", "manifest", "url")),
}


class UnsupportedDevice(ValueError):
    """Selected device lacks a supported trainer deployment method."""


def _description(entry):
    return " ".join(str(x or "") for x in (
        entry.original_name, entry.entity_id, entry.original_device_class
    )).lower().replace("_", " ")


def direct_entities(hass, device_id):
    """Return exact unique ESPHome trainer entities or None."""
    entities = er.async_entries_for_device(er.async_get(hass), device_id)
    found = {}
    for role, (domain, tokens) in REQUIRED.items():
        matches = [
            e.entity_id for e in entities
            if not e.disabled_by and e.entity_id.startswith(domain + ".")
            and (
                (role == CONF_BUTTON and "update" in _description(e)
                 and "model" in _description(e) and "wake" in _description(e))
                or (role != CONF_BUTTON and all(token in _description(e) for token in tokens))
            )
        ]
        if len(matches) != 1:
            return None
        found[role] = matches[0]
    # Optional capabilities are discovered without excluding older firmware.
    for role, domain, words in (
        ("runtime_hash_entity", "sensor", ("runtime", "model", "sha256")),
        ("trainer_url_entity", "text", ("trainer", "app", "url")),
    ):
        matches = [
            item.entity_id for item in entities
            if not item.disabled_by and item.entity_id.startswith(domain + ".")
            and all(word in _description(item) for word in words)
        ]
        if len(matches) == 1:
            found[role] = matches[0]
    return found


def device_adapter(hass, device_id):
    """Prefer direct firmware API, otherwise recognize compatible satellite stacks."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise UnsupportedDevice("Device no longer exists")
    direct = direct_entities(hass, device_id)
    if direct:
        return "esphome", direct

    # EchoLocal and techo5 expose the native ESPHome API, but not our custom
    # runtime update/capture entities. They consume HA custom_wake_words.
    # Some subdevices have no manufacturer, so walk their actual parent.
    current = device
    for _ in range(4):
        identity = " ".join(str(x or "") for x in (
            current.manufacturer, current.model, current.name,
            current.name_by_user, *[ident[0] for ident in current.identifiers]
        )).casefold()
        if "echolocal" in identity or "echo local" in identity:
            return "ha_catalog", {}
        if "techo5" in identity or "husker" in identity:
            return "ha_catalog", {}
        if not current.via_device_id:
            break
        current = dr.async_get(hass).async_get(current.via_device_id)
        if current is None:
            break
    raise UnsupportedDevice(
        "Select an ESPHome device running the trainer package, or an EchoLocal/techo5 device."
    )


def configured_entities(hass, settings):
    """Resolve device-based configurations, including older explicit-entity entries."""
    if settings.get(CONF_DEVICE):
        adapter, entities = device_adapter(hass, settings[CONF_DEVICE])
        if adapter != "esphome":
            raise UnsupportedDevice("This device receives models through Home Assistant's catalog")
        return entities
    return {role: settings[role] for role in REQUIRED}
