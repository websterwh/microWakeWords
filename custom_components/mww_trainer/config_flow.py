"""Configure a trainer and one or more compatible wake-word devices."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_URL, CONF_MODEL, CONF_DEVICE, CONF_EXTRA_TARGETS, CONF_AUTO
from .device import device_adapter, UnsupportedDevice


def selected_devices(settings):
    value = settings.get(CONF_DEVICE) or []
    return value if isinstance(value, list) else [value]


def schema(values=None):
    values = values or {}
    return vol.Schema({
        vol.Required(CONF_URL, default=values.get(CONF_URL, "http://192.168.1.2:8789")): str,
        vol.Required(CONF_MODEL, default=values.get(CONF_MODEL, "")): str,
        vol.Required(CONF_DEVICE, default=selected_devices(values)):
            selector.DeviceSelector(selector.DeviceSelectorConfig(multiple=True)),
        vol.Optional(CONF_EXTRA_TARGETS, default=values.get(CONF_EXTRA_TARGETS, "")): str,
        vol.Required(CONF_AUTO, default=values.get(CONF_AUTO, False)): bool,
    })


def validate(hass, data):
    if not data[CONF_URL].startswith(("http://", "https://")):
        return {"base": "invalid_url"}
    if not data[CONF_MODEL].strip():
        return {"base": "missing_model"}
    devices = selected_devices(data)
    if not devices:
        return {"base": "unsupported_device"}
    try:
        for device_id in devices:
            device_adapter(hass, device_id)
    except UnsupportedDevice:
        return {"base": "unsupported_device"}
    return {}


class TrainerFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            errors = validate(self.hass, user_input)
            if not errors:
                url = user_input[CONF_URL].strip().rstrip("/")
                model = user_input[CONF_MODEL].strip()
                await self.async_set_unique_id(f"{url}|{model.casefold()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Wake Word Trainer — {model}",
                    data={**user_input, CONF_URL: url, CONF_MODEL: model},
                )
            return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)
        return self.async_show_form(step_id="user", data_schema=schema())

    @staticmethod
    def async_get_options_flow(config_entry):
        return TrainerOptions(config_entry)


class TrainerOptions(config_entries.OptionsFlow):
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            errors = validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)
            return self.async_show_form(step_id="init", data_schema=schema(user_input), errors=errors)
        return self.async_show_form(
            step_id="init", data_schema=schema({**self.entry.data, **self.entry.options})
        )
