"""Resolve the trainer's ESPHome entities from one selected Home Assistant device."""
from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from .const import CONF_BUTTON, CONF_STATUS, CONF_COUNT, CONF_TARGETS, CONF_MANIFEST, CONF_DEVICE

# Prefer ESPHome's original entity name, which remains stable when the user
# customizes Home Assistant entity IDs and friendly names.
REQUIRED = {
    CONF_BUTTON: ("button", "update", "wake", "model"),
    CONF_STATUS: ("sensor", "model", "update", "status"),
    CONF_COUNT: ("sensor", "model", "update", "count"),
    CONF_TARGETS: ("text", "trainer", "active", "wake", "words"),
    CONF_MANIFEST: ("text", "trainer", "model", "manifest", "url"),
}


def resolve_device_entities(hass, device_id: str) -> dict[str, str]:
    """Return all five trainer entities, or raise an informative error.

    The names are matched by purpose, not by the wake-word name (Alexa, etc.).
    Restrict matching to entities belonging to the selected device.
    """
    registry = er.async_get(hass)
    entries = er.async_entries_for_device(registry, device_id)
    results = {}
    for role, words in REQUIRED.items():
        domain, *tokens = words
        matches = []
        for item in entries:
            if item.disabled_by or item.entity_id.split(".", 1)[0] != domain:
                continue
            display = (item.original_name or "").casefold().replace("_", " ")
            fallback = item.entity_id.split(".", 1)[-1].casefold().replace("_", " ")
            if all(token in display.split() for token in tokens) or (
                not item.original_name and all(token in fallback.split() for token in tokens)
            ):
                matches.append(item.entity_id)
        if len(matches) != 1:
            raise ValueError(
                f"Expected one {role} on the selected ESPHome device, found {len(matches)}. "
                "Ensure the updated ESPHome trainer package is installed and entities are enabled."
            )
        results[role] = matches[0]
    return results


def configured_entities(hass, settings: dict) -> dict[str, str]:
    """Resolve device-based entries, retaining backwards compatibility."""
    if settings.get(CONF_DEVICE):
        return resolve_device_entities(hass, settings[CONF_DEVICE])
    return {role: settings[role] for role in REQUIRED}
