"""Configure a local microWakeWord trainer and a Voice PE model updater."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DOMAIN, CONF_URL, CONF_MODEL, CONF_BUTTON, CONF_STATUS,
    CONF_COUNT, CONF_TARGETS, CONF_MANIFEST, CONF_EXTRA_TARGETS, CONF_AUTO,
)


class TrainerFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                return self.async_show_form(step_id="user", errors={"base": "invalid_url"}, data_schema=self._schema())
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Wake Word Trainer", data={**user_input, CONF_URL: url})
        return self.async_show_form(step_id="user", data_schema=self._schema())

    @staticmethod
    def _schema(data=None):
        data = data or {}
        return vol.Schema({
            vol.Required(CONF_URL, default=data.get(CONF_URL, "http://192.168.1.2:8789")): str,
            vol.Required(CONF_MODEL, default=data.get(CONF_MODEL, "alexa")): str,
            vol.Required(CONF_BUTTON, default=data.get(CONF_BUTTON, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="button")),
            vol.Required(CONF_STATUS, default=data.get(CONF_STATUS, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_COUNT, default=data.get(CONF_COUNT, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_MANIFEST, default=data.get(CONF_MANIFEST, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="text")),
            vol.Required(CONF_TARGETS, default=data.get(CONF_TARGETS, "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="text")),
            vol.Optional(CONF_EXTRA_TARGETS, default=data.get(CONF_EXTRA_TARGETS, "")): str,
            vol.Required(CONF_AUTO, default=data.get(CONF_AUTO, False)): bool,
        })

    @staticmethod
    def async_get_options_flow(config_entry):
        return TrainerOptions(config_entry)


class TrainerOptions(config_entries.OptionsFlow):
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=TrainerFlow._schema({**self.entry.data, **self.entry.options}),
        )
