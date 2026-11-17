"""Shared identity and availability."""
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

class EatonEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, kind, item_id, name):
        super().__init__(coordinator)
        self.kind, self.item_id = kind, item_id
        self._attr_unique_id = f'{coordinator.entry.unique_id}_{kind}_{item_id}'
        self._attr_name = name.strip()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)},
            manufacturer='Eaton', name='Eaton SecureConnect')

    @property
    def item(self):
        return self.coordinator.data.get(self.kind, {}).get(self.item_id, {})

    @property
    def available(self):
        return super().available and bool(self.item)
