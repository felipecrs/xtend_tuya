from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import Platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DPCode,
    TUYA_DISCOVERY_NEW,
)
from .util import (
    merge_device_descriptors
)
from .base import TuyaEntity
from .multi_manager.shared.device import (
    XTDevice,
)
from .multi_manager.multi_manager import (
    MultiManager,
    XTConfigEntry,
)

@dataclass(frozen=True)
class TuyaLockEntityDescription(LockEntityDescription):
    """Describes a Tuya lock."""
    pass

LOCKS: dict[str, tuple[TuyaLockEntityDescription, ...]] = {
    "cat": (
        TuyaLockEntityDescription(
            key=DPCode.LOCK,
            translation_key="lock",
        )
    )
}

async def async_setup_entry(
    hass: HomeAssistant, entry: XTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Tuya binary sensor dynamically through Tuya discovery."""
    hass_data = entry.runtime_data

    merged_descriptors = LOCKS
    for new_descriptor in entry.runtime_data.multi_manager.get_platform_descriptors_to_merge(Platform.BINARY_SENSOR):
        merged_descriptors = merge_device_descriptors(merged_descriptors, new_descriptor)

    @callback
    def async_discover_device(device_map) -> None:
        """Discover and add a discovered Tuya binary sensor."""
        entities: list[TuyaLockEntity] = []
        device_ids = [*device_map]
        for device_id in device_ids:
            if device := hass_data.manager.device_map.get(device_id, None):
                if descriptions := merged_descriptors.get(device.category):
                    for description in descriptions:
                        dpcode = description.dpcode or description.key
                        if dpcode in device.status:
                            entities.append(
                                TuyaLockEntity(
                                    device, hass_data.manager, description
                                )
                            )

        async_add_entities(entities)

    hass_data.manager.register_device_descriptors("binary_sensors", merged_descriptors)
    async_discover_device([*hass_data.manager.device_map])
    #async_discover_device(hass_data.manager, hass_data.manager.open_api_device_map)

    entry.async_on_unload(
        async_dispatcher_connect(hass, TUYA_DISCOVERY_NEW, async_discover_device)
    )

class TuyaLockEntity(TuyaEntity, LockEntity):
    """Tuya Binary Sensor Entity."""

    entity_description: TuyaLockEntityDescription

    def __init__(
        self,
        device: XTDevice,
        device_manager: MultiManager,
        description: TuyaLockEntityDescription,
    ) -> None:
        """Init Tuya binary sensor."""
        super().__init__(device, device_manager)
        self.entity_description = description
        self._attr_unique_id = f"{super().unique_id}{description.key}"

    def lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        raise NotImplementedError
    
    def unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        raise NotImplementedError
    
    def open(self, **kwargs: Any) -> None:
        """Open the door latch."""
        raise NotImplementedError