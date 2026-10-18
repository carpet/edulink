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

from .const import (
    CONF_ACADEMIC_YEAR_START,
    CONF_HOMEWORK_DAYS_AHEAD,
    CONF_KIT_RULES,
    CURRENCY,
    DEFAULT_ACADEMIC_YEAR_START,
    DEFAULT_HOMEWORK_DAYS_AHEAD,
    DEFAULT_KIT_RULES,
    OVERDUE_WINDOW_DAYS,
)
from .coordinator import EdulinkConfigEntry, StatusCoordinator, school_now
from .entity import EdulinkAccountEntity, EdulinkEntity
from .models import (
    SCHOOL_TZ,
    Attendance,
    Catering,
    Clubs,
    Homework,
    HomeworkList,
    KitRules,
    RecordEvents,
    SchoolDay,
    Status,
    Timetable,
    academic_year_start,
    kit_for,
    parse_kit_rules,
)

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
        key="lessons_today",
        translation_key="lessons_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda tt, now: len(d.teaching) if (d := tt.day(now.date())) else 0,
        attrs_fn=lambda tt, now: _day_attrs(tt, now.date()),
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


def _kit_attrs(day: SchoolDay | None, rules: KitRules) -> dict[str, Any]:
    return {
        "date": day.date.isoformat() if day else None,
        "items": kit_for(day.teaching, rules) if day else [],
    }


def _timetable_sensors(
    rules: KitRules,
) -> tuple[EdulinkSensorDescription[Timetable], ...]:
    """Timetable sensors, with kit reminders from the kit rules option."""

    def today(tt: Timetable, now: datetime) -> SchoolDay | None:
        return tt.day(now.date())

    def next_day(tt: Timetable, now: datetime) -> SchoolDay | None:
        return tt.next_school_day(now.date())

    return (
        *TIMETABLE_SENSORS,
        EdulinkSensorDescription(
            key="kit_today",
            translation_key="kit_today",
            value_fn=lambda tt, now: len(_kit_attrs(today(tt, now), rules)["items"]),
            attrs_fn=lambda tt, now: _kit_attrs(today(tt, now), rules),
        ),
        EdulinkSensorDescription(
            key="kit_next_school_day",
            translation_key="kit_next_school_day",
            value_fn=lambda tt, now: len(_kit_attrs(next_day(tt, now), rules)["items"]),
            attrs_fn=lambda tt, now: _kit_attrs(next_day(tt, now), rules),
        ),
    )


# ----------------------------------------------------------------- homework


def _homework_list(items: list[Homework], today: date) -> dict[str, Any]:
    return {
        "homework": [
            {**hw.as_dict(), "days_until_due": (hw.due - today).days} for hw in items
        ]
    }


def _next_due(hw: HomeworkList) -> Homework | None:
    outstanding = hw.outstanding()
    return outstanding[0] if outstanding else None


def _next_due_attrs(hw: HomeworkList, _now: datetime) -> dict[str, Any] | None:
    item = _next_due(hw)
    if not item:
        return None
    return {**item.as_dict(), "description": item.description}


def _homework_sensors(
    days_ahead: int,
) -> tuple[EdulinkSensorDescription[HomeworkList], ...]:
    """Homework sensors. Outstanding only counts homework due within days_ahead."""

    def until(now: datetime) -> date:
        return now.date() + timedelta(days=days_ahead)

    return (
        EdulinkSensorDescription(
            key="homework_outstanding",
            translation_key="homework_outstanding",
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda hw, now: len(hw.outstanding(until(now))),
            attrs_fn=lambda hw, now: _homework_list(
                hw.outstanding(until(now)), now.date()
            ),
        ),
        EdulinkSensorDescription(
            key="homework_urgency",
            translation_key="homework_urgency",
            device_class=SensorDeviceClass.ENUM,
            options=["none", "later", "tomorrow", "today"],
            value_fn=lambda hw, now: hw.urgency(now.date(), until(now)),
        ),
        *HOMEWORK_SENSORS,
    )


HOMEWORK_SENSORS: tuple[EdulinkSensorDescription[HomeworkList], ...] = (
    EdulinkSensorDescription(
        key="homework_due_today",
        translation_key="homework_due_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda hw, now: len(hw.due_on(now.date())),
        attrs_fn=lambda hw, now: _homework_list(hw.due_on(now.date()), now.date()),
    ),
    EdulinkSensorDescription(
        key="homework_due_tomorrow",
        translation_key="homework_due_tomorrow",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda hw, now: len(hw.due_on(_tomorrow(now))),
        attrs_fn=lambda hw, now: _homework_list(hw.due_on(_tomorrow(now)), now.date()),
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
            hw.overdue(now.date(), OVERDUE_WINDOW_DAYS), now.date()
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
    return {"amount": c.top_ups[0].total} if c.top_ups else None


def _today_items(c: Catering, now: datetime) -> dict[str, Any]:
    since = _start_of_day(now)
    today = [t for t in c.purchases if t.when >= since]
    return {
        "purchases": [t.as_dict() for t in today],
        # Till item names are upper case.
        "items": [item.name.title() for t in reversed(today) for item in t.items],
    }


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
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, _now: c.top_ups[0].when if c.top_ups else None,
        attrs_fn=_last_top_up_attrs,
    ),
    EdulinkSensorDescription(
        key="last_top_up_amount",
        translation_key="last_top_up_amount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, _now: c.top_ups[0].total if c.top_ups else None,
    ),
)


# ------------------------------------------------------------ school record
#
# Disabled by default: these describe a child's school record, and Home
# Assistant keeps history indefinitely. Only counts, points and dates.


def _record_sensors(
    start_month: int,
) -> tuple[
    tuple[EdulinkSensorDescription[RecordEvents], ...],
    tuple[EdulinkSensorDescription[RecordEvents], ...],
]:
    """Achievement and behaviour sensors, with the academic year from options."""

    def year_start(now: datetime) -> datetime:
        return datetime.combine(
            academic_year_start(now.date(), start_month), time(), SCHOOL_TZ
        )

    achievement = (
        EdulinkSensorDescription[RecordEvents](
            key="achievement_points",
            translation_key="achievement_points",
            state_class=SensorStateClass.TOTAL,
            entity_registry_enabled_default=False,
            value_fn=lambda r, now: r.points_since(year_start(now).date()),
            last_reset_fn=year_start,
        ),
        EdulinkSensorDescription[RecordEvents](
            key="achievements_this_week",
            translation_key="achievements_this_week",
            state_class=SensorStateClass.TOTAL,
            entity_registry_enabled_default=False,
            value_fn=lambda r, now: len(r.since(_start_of_week(now).date())),
            last_reset_fn=_start_of_week,
        ),
        EdulinkSensorDescription[RecordEvents](
            key="last_achievement",
            translation_key="last_achievement",
            device_class=SensorDeviceClass.DATE,
            entity_registry_enabled_default=False,
            value_fn=lambda r, _now: r.latest,
        ),
    )
    behaviour = (
        EdulinkSensorDescription[RecordEvents](
            key="behaviour_events",
            translation_key="behaviour_events",
            state_class=SensorStateClass.TOTAL,
            entity_registry_enabled_default=False,
            value_fn=lambda r, now: len(r.since(year_start(now).date())),
            last_reset_fn=year_start,
        ),
        EdulinkSensorDescription[RecordEvents](
            key="last_behaviour_event",
            translation_key="last_behaviour_event",
            device_class=SensorDeviceClass.DATE,
            entity_registry_enabled_default=False,
            value_fn=lambda r, _now: r.latest,
        ),
    )
    return achievement, behaviour


ATTENDANCE_SENSORS: tuple[EdulinkSensorDescription[Attendance], ...] = (
    EdulinkSensorDescription(
        key="last_absence",
        translation_key="last_absence",
        device_class=SensorDeviceClass.DATE,
        entity_registry_enabled_default=False,
        value_fn=lambda a, _now: a.last_absence,
    ),
)

# -------------------------------------------------------------------- clubs


def _next_club_attrs(c: Clubs, now: datetime) -> dict[str, Any]:
    session = c.next_session(now)
    return {"club": session.club if session else None, "clubs": c.names}


CLUB_SENSORS: tuple[EdulinkSensorDescription[Clubs], ...] = (
    EdulinkSensorDescription(
        key="next_club_session",
        translation_key="next_club_session",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, now: s.start if (s := c.next_session(now)) else None,
        attrs_fn=_next_club_attrs,
    ),
)

# ------------------------------------------------------------------- status


@dataclass(frozen=True, kw_only=True)
class EdulinkStatusSensorDescription(SensorEntityDescription):
    """An account-wide count from EduLink.Status."""

    value_fn: Callable[[Status], int]


STATUS_SENSORS: tuple[EdulinkStatusSensorDescription, ...] = (
    EdulinkStatusSensorDescription(
        key="new_messages",
        translation_key="new_messages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.new_messages,
    ),
    EdulinkStatusSensorDescription(
        key="new_forms",
        translation_key="new_forms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.new_forms,
    ),
    EdulinkStatusSensorDescription(
        key="new_noticeboard_items",
        translation_key="new_noticeboard_items",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.new_noticeboard_items,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    runtime = entry.runtime_data
    achievement, behaviour = _record_sensors(
        int(entry.options.get(CONF_ACADEMIC_YEAR_START, DEFAULT_ACADEMIC_YEAR_START))
    )
    entities: list[SensorEntity] = []
    for coordinator, descriptions in (
        (
            runtime.timetable,
            _timetable_sensors(
                parse_kit_rules(entry.options.get(CONF_KIT_RULES, DEFAULT_KIT_RULES))
            ),
        ),
        (
            runtime.homework,
            _homework_sensors(
                int(
                    entry.options.get(
                        CONF_HOMEWORK_DAYS_AHEAD, DEFAULT_HOMEWORK_DAYS_AHEAD
                    )
                )
            ),
        ),
        (runtime.catering, CATERING_SENSORS),
        (runtime.achievement, achievement),
        (runtime.behaviour, behaviour),
        (runtime.attendance, ATTENDANCE_SENSORS),
        (runtime.clubs, CLUB_SENSORS),
    ):
        if coordinator is None:
            continue
        entities.extend(
            EdulinkSensor(coordinator, learner, description)
            for learner in runtime.learners
            for description in descriptions
        )
    if runtime.status is not None:
        entities.extend(
            EdulinkStatusSensor(runtime.status, description)
            for description in STATUS_SENSORS
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


class EdulinkStatusSensor(EdulinkAccountEntity, SensorEntity):
    """An account-wide count."""

    entity_description: EdulinkStatusSensorDescription

    def __init__(
        self,
        coordinator: StatusCoordinator,
        description: EdulinkStatusSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description)

    @property
    def native_value(self) -> int | None:
        """Return the state."""
        status = self.coordinator.data
        return self.entity_description.value_fn(status) if status else None
