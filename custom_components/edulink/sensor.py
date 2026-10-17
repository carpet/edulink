"""Sensors for Edulink One."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import CURRENCY, OVERDUE_WINDOW_DAYS
from .coordinator import EdulinkConfigEntry, school_now
from .entity import EdulinkEntity
from .models import SCHOOL_TZ, Catering, Homework, HomeworkList, Timetable

PARALLEL_UPDATES = 0

type SensorValue = StateType | date | datetime


@dataclass(frozen=True, kw_only=True)
class EdulinkSensorDescription[T](SensorEntityDescription):
    """Sensor description with value and attribute functions."""

    value_fn: Callable[[T, datetime], SensorValue]
    attrs_fn: Callable[[T, datetime], dict[str, Any] | None] = lambda _d, _n: None
    last_reset_fn: Callable[[datetime], datetime] | None = None


# ---------------------------------------------------------------- timetable


def _current_lesson_attrs(tt: Timetable, now: datetime) -> dict[str, Any] | None:
    _state, lesson = tt.current_state(now)
    return lesson.as_dict() if lesson else None


def _next_lesson_attrs(tt: Timetable, now: datetime) -> dict[str, Any] | None:
    lesson = tt.next_lesson(now)
    return lesson.as_dict() if lesson else None


def _day_attrs(tt: Timetable, day: date | None) -> dict[str, Any] | None:
    school_day = tt.day(day) if day else None
    if not school_day:
        return None
    return {
        "day_name": school_day.name,
        "lessons": [lesson.as_dict() for lesson in school_day.teaching],
    }


def _tomorrow(now: datetime) -> date:
    return now.date() + timedelta(days=1)


TIMETABLE_SENSORS: tuple[EdulinkSensorDescription[Timetable], ...] = (
    EdulinkSensorDescription(
        key="current_lesson",
        translation_key="current_lesson",
        value_fn=lambda tt, now: tt.current_state(now)[0],
        attrs_fn=_current_lesson_attrs,
    ),
    EdulinkSensorDescription(
        key="next_lesson",
        translation_key="next_lesson",
        value_fn=lambda tt, now: l.subject if (l := tt.next_lesson(now)) else None,
        attrs_fn=_next_lesson_attrs,
    ),
    EdulinkSensorDescription(
        key="school_day_start",
        translation_key="school_day_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda tt, now: d.start if (d := tt.current_day(now)) else None,
    ),
    EdulinkSensorDescription(
        key="school_day_end",
        translation_key="school_day_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda tt, now: d.end if (d := tt.current_day(now)) else None,
    ),
    EdulinkSensorDescription(
        key="next_school_day",
        translation_key="next_school_day",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda tt, now: (
            d.date if (d := tt.next_school_day(now.date())) else None
        ),
        attrs_fn=lambda tt, now: _day_attrs(
            tt, d.date if (d := tt.next_school_day(now.date())) else None
        ),
    ),
    EdulinkSensorDescription(
        key="lessons_tomorrow",
        translation_key="lessons_tomorrow",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda tt, now: (
            len(d.teaching) if (d := tt.day(_tomorrow(now))) else 0
        ),
        attrs_fn=lambda tt, now: _day_attrs(tt, _tomorrow(now)),
    ),
    EdulinkSensorDescription(
        key="timetable_week",
        translation_key="timetable_week",
        value_fn=lambda tt, now: d.cycle_week if (d := tt.current_day(now)) else None,
    ),
)

# ----------------------------------------------------------------- homework


def _homework_list(items: list[Homework]) -> dict[str, Any]:
    return {"homework": [hw.as_dict() for hw in items]}


def _next_due(hw: HomeworkList) -> Homework | None:
    outstanding = hw.outstanding()
    return outstanding[0] if outstanding else None


def _next_due_attrs(hw: HomeworkList, _now: datetime) -> dict[str, Any] | None:
    item = _next_due(hw)
    if not item:
        return None
    return {**item.as_dict(), "description": item.description}


HOMEWORK_SENSORS: tuple[EdulinkSensorDescription[HomeworkList], ...] = (
    EdulinkSensorDescription(
        key="homework_outstanding",
        translation_key="homework_outstanding",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda hw, _now: len(hw.outstanding()),
        attrs_fn=lambda hw, _now: _homework_list(hw.outstanding()),
    ),
    EdulinkSensorDescription(
        key="homework_due_today",
        translation_key="homework_due_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda hw, now: len(hw.due_on(now.date())),
        attrs_fn=lambda hw, now: _homework_list(hw.due_on(now.date())),
    ),
    EdulinkSensorDescription(
        key="homework_due_tomorrow",
        translation_key="homework_due_tomorrow",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda hw, now: len(hw.due_on(_tomorrow(now))),
        attrs_fn=lambda hw, now: _homework_list(hw.due_on(_tomorrow(now))),
    ),
    EdulinkSensorDescription(
        key="next_homework_due",
        translation_key="next_homework_due",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda hw, _now: item.due if (item := _next_due(hw)) else None,
        attrs_fn=_next_due_attrs,
    ),
    EdulinkSensorDescription(
        key="homework_overdue",
        translation_key="homework_overdue",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda hw, now: len(hw.overdue(now.date(), OVERDUE_WINDOW_DAYS)),
        attrs_fn=lambda hw, now: _homework_list(
            hw.overdue(now.date(), OVERDUE_WINDOW_DAYS)
        ),
    ),
)

# ----------------------------------------------------------------- catering


def _start_of_day(now: datetime) -> datetime:
    return datetime.combine(now.date(), time(), SCHOOL_TZ)


def _start_of_week(now: datetime) -> datetime:
    return _start_of_day(now) - timedelta(days=now.weekday())


def _last_purchase_attrs(c: Catering, _now: datetime) -> dict[str, Any] | None:
    return c.purchases[0].as_dict() if c.purchases else None


def _last_top_up_attrs(c: Catering, _now: datetime) -> dict[str, Any] | None:
    return {"time": c.top_ups[0].when.isoformat()} if c.top_ups else None


def _today_items(c: Catering, now: datetime) -> dict[str, Any]:
    since = _start_of_day(now)
    return {"purchases": [t.as_dict() for t in c.purchases if t.when >= since]}


CATERING_SENSORS: tuple[EdulinkSensorDescription[Catering], ...] = (
    EdulinkSensorDescription(
        key="catering_balance",
        translation_key="catering_balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, _now: c.balance,
    ),
    EdulinkSensorDescription(
        key="last_purchase",
        translation_key="last_purchase",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, _now: c.purchases[0].when if c.purchases else None,
        attrs_fn=_last_purchase_attrs,
    ),
    EdulinkSensorDescription(
        key="spent_today",
        translation_key="spent_today",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, now: c.spent_since(_start_of_day(now)),
        attrs_fn=_today_items,
        last_reset_fn=_start_of_day,
    ),
    EdulinkSensorDescription(
        key="spent_this_week",
        translation_key="spent_this_week",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, now: c.spent_since(_start_of_week(now)),
        last_reset_fn=_start_of_week,
    ),
    EdulinkSensorDescription(
        key="last_top_up",
        translation_key="last_top_up",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, _now: c.top_ups[0].total if c.top_ups else None,
        attrs_fn=_last_top_up_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    runtime = entry.runtime_data
    entities: list[EdulinkSensor[Any]] = []
    for coordinator, descriptions in (
        (runtime.timetable, TIMETABLE_SENSORS),
        (runtime.homework, HOMEWORK_SENSORS),
        (runtime.catering, CATERING_SENSORS),
    ):
        if coordinator is None:
            continue
        entities.extend(
            EdulinkSensor(coordinator, learner, description)
            for learner in runtime.learners
            for description in descriptions
        )
    async_add_entities(entities)


class EdulinkSensor[T](EdulinkEntity[T], SensorEntity):
    """An Edulink sensor."""

    entity_description: EdulinkSensorDescription[T]

    @property
    def native_value(self) -> SensorValue:
        """Return the state."""
        return self.entity_description.value_fn(self.learner_data, school_now())

    @property
    def last_reset(self) -> datetime | None:
        """Start of the period a total covers."""
        if (last_reset_fn := self.entity_description.last_reset_fn) is None:
            return None
        return last_reset_fn(school_now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attrs_fn(self.learner_data, school_now())
