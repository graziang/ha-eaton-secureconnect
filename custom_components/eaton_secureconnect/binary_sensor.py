"""Zone states, usable as ordinary HA state triggers."""
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .entity import EatonEntity
from .model import contact_state

async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([EatonFault(c)])
    known = set()
    def discover():
        new = [EatonZone(c, i, z.get('name', f'Zona {i}')) for i, z in c.data['zones'].items() if i not in known]
        known.update(c.data['zones'])
        async_add_entities(new)
    discover()
    entry.async_on_unload(c.async_add_listener(discover))

class EatonZone(EatonEntity, BinarySensorEntity):
    # Generic opening: zone names do not reliably identify contact vs PIR.
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator, item_id, name):
        super().__init__(coordinator, 'zones', item_id, name)
        from homeassistant.helpers.entity import DeviceInfo
        from .const import DOMAIN
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'{coordinator.entry.unique_id}_zone_{item_id}')},
            name=name.strip(), manufacturer='Eaton', model='Zona SecureConnect',
            via_device_id=coordinator.device_id)

    @property
    def is_on(self):
        return contact_state(self.item)

    @property
    def extra_state_attributes(self):
        return {'zone_id': self.item_id, 'partition_id': self.coordinator.data['zone_partitions'].get(self.item_id),
                'omitted': self.item.get('omitted')}


class EatonFault(EatonEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, 'system', 'faults', 'Guasti centrale')

    @property
    def available(self):
        return self.coordinator.last_update_success

    @property
    def is_on(self):
        return bool(self.coordinator.data['faults'])

    @property
    def extra_state_attributes(self):
        return {'count': len(self.coordinator.data['faults']),
                'event_gap': self.coordinator.data.get('event_gap', False)}
