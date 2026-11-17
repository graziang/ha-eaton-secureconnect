"""Discoverable event entity alongside the structured event bus."""
from homeassistant.components.event import EventEntity
from homeassistant.core import callback
from .const import EVENT
from .entity import EatonEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EatonEvent(entry.runtime_data)])

class EatonEvent(EatonEntity, EventEntity):
    _attr_event_types = ['log']

    def __init__(self, coordinator):
        super().__init__(coordinator, 'system', 'events', 'Evento centrale')

    @property
    def available(self):
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        @callback
        def received(event):
            if event.data.get('entry_id') == self.coordinator.entry.entry_id:
                self._trigger_event('log', event.data['log'])
                self.async_write_ha_state()
        self.async_on_remove(self.hass.bus.async_listen(EVENT, received))
