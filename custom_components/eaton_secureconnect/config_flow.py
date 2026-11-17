"""UI setup and reauthentication for an existing dedicated app pairing."""
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .api import ProbeError
from .const import DOMAIN, DEFAULT_INTERVAL
from .session import Session, AuthError

try:
    from .local_defaults import APPLICATION_ID
except ImportError:
    APPLICATION_ID = ""


def schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema({
        vol.Required('username', default=defaults.get('username', '')): str,
        vol.Required('password'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required('application_id', default=defaults.get('application_id', APPLICATION_ID)): str,
        vol.Required('pairing_name', default=defaults.get('pairing_name', 'HomeAssistantTest')): str,
    })


def validate(data):
    session = Session(data)
    try:
        session.read()
    finally:
        session.close()


class EatonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input['application_id'] = str(uuid.UUID(user_input['application_id']))
                user_input['username'] = user_input['username'].strip()
                user_input['pairing_name'] = user_input['pairing_name'].strip()
                await self.async_set_unique_id(f"{user_input['application_id']}_{user_input['pairing_name']}")
                self._abort_if_unique_id_configured()
                await self.hass.async_add_executor_job(validate, user_input)
            except ValueError:
                errors['application_id'] = 'invalid_identity'
            except AuthError:
                errors['base'] = 'invalid_auth'
            except ProbeError:
                errors['base'] = 'cannot_connect'
            else:
                return self.async_create_entry(title='Eaton SecureConnect', data=user_input)
        return self.async_show_form(step_id='user', data_schema=schema(user_input), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input:
            data = {**entry.data, **user_input}
            try:
                await self.hass.async_add_executor_job(validate, data)
            except AuthError:
                errors['base'] = 'invalid_auth'
            except ProbeError:
                errors['base'] = 'cannot_connect'
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(step_id='reauth_confirm', data_schema=vol.Schema({
            vol.Required('username', default=entry.data['username']): str,
            vol.Required('password'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EatonOptionsFlow()


class EatonOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)
        return self.async_show_form(step_id='init', data_schema=vol.Schema({
            vol.Required('poll_interval', default=self.config_entry.options.get('poll_interval', DEFAULT_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=15, max=300)),
            vol.Required('enable_controls', default=self.config_entry.options.get('enable_controls', False)): bool,
        }))
