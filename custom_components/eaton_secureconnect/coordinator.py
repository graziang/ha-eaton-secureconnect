"""One shared poll for all panel entities."""
from datetime import timedelta
import logging
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import ProbeError
from .const import DOMAIN, EVENT, DEFAULT_INTERVAL
from .model import EventCursor
from .session import Session, AuthError


class EatonCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, session):
        super().__init__(hass, logging.getLogger(__name__), name=DOMAIN,
            config_entry=entry, update_interval=timedelta(seconds=entry.options.get('poll_interval', DEFAULT_INTERVAL)))
        self.entry = entry
        self.session = session
        self.cursor = EventCursor()

    async def _async_update_data(self):
        try:
            data = await self.hass.async_add_executor_job(self.session.read)
        except AuthError as err:
            self.cursor.reset()
            raise ConfigEntryAuthFailed(str(err)) from None
        except ProbeError as err:
            self.cursor.reset()
            raise UpdateFailed(str(err)) from None
        for event in self.cursor.consume(data['logs']):
            self.hass.bus.async_fire(EVENT, {'entry_id': self.entry.entry_id, 'log': event})
        data['event_gap'] = self.cursor.gap
        return data

    async def command(self, area_id, action):
        if not self.entry.options.get('enable_controls', False):
            raise HomeAssistantError('Abilitare i comandi nelle opzioni dell’integrazione.')
        if not self.last_update_success:
            raise HomeAssistantError('Centrale non disponibile: aggiornare prima di riprovare.')
        try:
            await self.hass.async_add_executor_job(self.session.command, area_id, action)
        except ProbeError as err:
            await self.async_request_refresh()
            raise HomeAssistantError(str(err)) from None
        await self.async_request_refresh()
        if not self.last_update_success:
            raise HomeAssistantError('Comando inviato, stato non verificabile. Controllare nell’app Eaton.')
        if self.data['partitions'].get(area_id, {}).get('state') != action:
            raise HomeAssistantError('Comando inviato, stato finale non ancora confermato. Attendere e verificare senza reinviare.')
