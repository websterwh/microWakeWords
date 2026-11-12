"""UI setup: choose the local trainer, model name and compatible satellite."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_URL, CONF_MODEL, CONF_DEVICE, CONF_EXTRA_TARGETS, CONF_AUTO
from .device import device_adapter, UnsupportedDevice


def schema(values=None):
    values = values or {}
    return vol.Schema({
        vol.Required(CONF_URL, default=values.get(CONF_URL, "http://192.168.1.2:8789")): str,
        vol.Required(CONF_MODEL, default=values.get(CONF_MODEL, "")): str,
        vol.Required(CONF_DEVICE, default=values.get(CONF_DEVICE, vol.UNDEFINED)):
            selector.DeviceSelector(selector.DeviceSelectorConfig()),
        vol.Optional(CONF_EXTRA_TARGETS, default=values.get(CONF_EXTRA_TARGETS, "")): str,
        vol.Required(CONF_AUTO, default=values.get(CONF_AUTO, False)): bool,
    })


class TrainerFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            errors = {}
            if not url.startswith(("http://", "https://")):
                errors["base"] = "invalid_url"
            elif not user_input[CONF_MODEL].strip():
                errors["base"] = "missing_model"
            else:
                try:
                    device_adapter(self.hass, user_input[CONF_DEVICE])
                except UnsupportedDevice:
                    errors["base"] = "unsupported_device"
            if not errors:
                await self.async_set_unique_id(f"{url}|{user_input[CONF_DEVICE]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Wake Word Trainer",
                    data={**user_input, CONF_URL: url},
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
            errors = {}
            try:
                device_adapter(self.hass, user_input[CONF_DEVICE])
            except UnsupportedDevice:
                errors["base"] = "unsupported_device"
            if not errors:
                return self.async_create_entry(title="", data=user_input)
            return self.async_show_form(step_id="init", data_schema=schema(user_input), errors=errors)
        return self.async_show_form(
            step_id="init", data_schema=schema({**self.entry.data, **self.entry.options})
        )
