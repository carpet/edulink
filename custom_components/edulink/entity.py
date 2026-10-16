"""Base entity for Edulink One."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Learner
from .const import DOMAIN
from .coordinator import EdulinkCoordinator


class EdulinkEntity[T](CoordinatorEntity[EdulinkCoordinator[T]]):
    """An entity belonging to one learner's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EdulinkCoordinator[T],
        learner: Learner,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.learner = learner
        runtime = coordinator.config_entry.runtime_data
        self._attr_unique_id = (
            f"{runtime.establishment_id}_{learner.id}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{runtime.establishment_id}_{learner.id}")},
            name=learner.forename or learner.id,
            manufacturer="Edulink One",
            model=runtime.school_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Available while the coordinator has data for this learner."""
        return super().available and self.learner.id in (self.coordinator.data or {})

    @property
    def learner_data(self) -> T:
        """This learner's slice of the coordinator data."""
        return self.coordinator.data[self.learner.id]
