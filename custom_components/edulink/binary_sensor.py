"""Binary sensors for Edulink One."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_LOW_BALANCE, DEFAULT_LOW_BALANCE
from .coordinator import EdulinkConfigEntry, school_now
from .entity import EdulinkEntity
from .models import Catering, Timetable

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EdulinkBinarySensorDescription[T](BinarySensorEntityDescription):
    """Binary sensor description. options are the config entry options."""

    is_on_fn: Callable[[T, datetime, dict[str, Any]], bool | None]
    attrs_fn: Callable[[T, datetime, dict[str, Any]], dict[str, Any] | None] = (
        lambda _d, _n, _o: None
    )


def _day_times(tt: Timetable, now: datetime, offset: int) -> dict[str, Any] | None:
    day = tt.day(now.date() + timedelta(days=offset))
    if not day or not day.start or not day.end:
        return None
    return {"start": day.start.isoformat(), "end": day.end.isoformat()}


def _threshold(options: dict[str, Any]) -> float:
    return float(options.get(CONF_LOW_BALANCE, DEFAULT_LOW_BALANCE))


TIMETABLE_BINARY_SENSORS: tuple[EdulinkBinarySensorDescription[Timetable], ...] = (
    EdulinkBinarySensorDescription(
        key="school_today",
        translation_key="school_today",
        is_on_fn=lambda tt, now, _o: tt.day(now.date()) is not None,
        attrs_fn=lambda tt, now, _o: _day_times(tt, now, 0),
    ),
    EdulinkBinarySensorDescription(
        key="school_tomorrow",
        translation_key="school_tomorrow",
        is_on_fn=lambda tt, now, _o: tt.day(now.date() + timedelta(days=1)) is not None,
        attrs_fn=lambda tt, now, _o: _day_times(tt, now, 1),
    ),
)

CATERING_BINARY_SENSORS: tuple[EdulinkBinarySensorDescription[Catering], ...] = (
    EdulinkBinarySensorDescription(
        key="low_catering_balance",
        translation_key="low_catering_balance",
        is_on_fn=lambda c, _n, o: (
            None if c.balance is None else c.balance < _threshold(o)
        ),
        attrs_fn=lambda _c, _n, o: {"threshold": _threshold(o)},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    runtime = entry.runtime_data
    entities: list[EdulinkBinarySensor[Any]] = []
    for coordinator, descriptions in (
        (runtime.timetable, TIMETABLE_BINARY_SENSORS),
        (runtime.catering, CATERING_BINARY_SENSORS),
    ):
        if coordinator is None:
            continue
        entities.extend(
            EdulinkBinarySensor(coordinator, learner, description)
            for learner in runtime.learners
            for description in descriptions
        )
    async_add_entities(entities)


class EdulinkBinarySensor[T](EdulinkEntity[T], BinarySensorEntity):
    """An Edulink binary sensor."""

    entity_description: EdulinkBinarySensorDescription[T]

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.is_on_fn(
            self.learner_data, school_now(), dict(self.coordinator.config_entry.options)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attrs_fn(
            self.learner_data, school_now(), dict(self.coordinator.config_entry.options)
        )
