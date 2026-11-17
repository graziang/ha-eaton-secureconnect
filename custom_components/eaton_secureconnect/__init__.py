"""Eaton SecureConnect cloud integration."""
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import ConfigEntryError
from .const import DOMAIN
from .coordinator import EatonCoordinator
from .session import Session

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.EVENT]

async def async_setup_entry(hass, entry):
    session = await hass.async_add_executor_job(Session, entry.data)
    coordinator = EatonCoordinator(hass, entry, session)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await hass.async_add_executor_job(session.close)
        raise
    coordinator.device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.unique_id)},
        name="Eaton SecureConnect", manufacturer="Eaton").id
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_update_options))

async def _update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.async_add_executor_job(entry.runtime_data.session.close)
        return
    raise ConfigEntryError("Impossibile scaricare le piattaforme Eaton")
