"""Base entity for Edulink One."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Learner
from .const import CONF_USER_ID, DOMAIN
from .coordinator import EdulinkCoordinator, StatusCoordinator


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
        """Available while the coordinator has usable data for this learner."""
        return self.coordinator.is_usable(self.learner.id)

    @property
    def learner_data(self) -> T:
        """This learner's slice of the coordinator data."""
        return self.coordinator.data[self.learner.id]


class EdulinkAccountEntity(CoordinatorEntity[StatusCoordinator]):
    """An entity for the Edulink account as a whole, rather than one learner."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StatusCoordinator, description: EntityDescription
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        account = f"{entry.runtime_data.establishment_id}_{entry.data[CONF_USER_ID]}"
        self._attr_unique_id = f"{account}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account)},
            name="Edulink One",
            manufacturer="Edulink One",
            model=entry.runtime_data.school_name,
            entry_type=DeviceEntryType.SERVICE,
        )
