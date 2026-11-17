"""Native per-partition alarm controls; no forced or bypass modes."""
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity, AlarmControlPanelEntityFeature, AlarmControlPanelState
from .entity import EatonEntity
from .model import alarm_state

async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    known = set()
    def discover():
        new = [EatonAlarm(c, i, p.get('name', f'Area {i}')) for i, p in c.data['partitions'].items() if i not in known]
        known.update(c.data['partitions'])
        async_add_entities(new)
    discover()
    entry.async_on_unload(c.async_add_listener(discover))

class EatonAlarm(EatonEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False

    def __init__(self, coordinator, item_id, name):
        super().__init__(coordinator, 'partitions', item_id, name)

    @property
    def supported_features(self):
        return AlarmControlPanelEntityFeature.ARM_AWAY if self.coordinator.entry.options.get('enable_controls', False) else AlarmControlPanelEntityFeature(0)

    @property
    def alarm_state(self):
        state = alarm_state(self.item)
        return AlarmControlPanelState(state) if state else None

    @property
    def extra_state_attributes(self):
        return {'partition_id': self.item_id, 'raw_state': self.item.get('state'),
                'is_fault': self.item.get('is-fault'),
                'controls_enabled': self.coordinator.entry.options.get('enable_controls', False)}

    async def async_alarm_arm_away(self, code=None):
        await self.coordinator.command(self.item_id, 'fullset')

    async def async_alarm_disarm(self, code=None):
        await self.coordinator.command(self.item_id, 'unset')
