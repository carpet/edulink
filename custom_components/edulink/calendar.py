"""Calendars for Edulink One: lessons and homework due dates."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EdulinkConfigEntry, school_now
from .entity import EdulinkEntity
from .models import HomeworkList, Timetable

PARALLEL_UPDATES = 0

TIMETABLE_CALENDAR = CalendarEntityDescription(
    key="timetable", translation_key="timetable"
)
HOMEWORK_CALENDAR = CalendarEntityDescription(
    key="homework", translation_key="homework"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up calendars."""
    runtime = entry.runtime_data
    entities: list[CalendarEntity] = []
    for learner in runtime.learners:
        if runtime.timetable:
            entities.append(
                TimetableCalendar(runtime.timetable, learner, TIMETABLE_CALENDAR)
            )
        if runtime.homework:
            entities.append(
                HomeworkCalendar(runtime.homework, learner, HOMEWORK_CALENDAR)
            )
    async_add_entities(entities)


def _upcoming(events: list[CalendarEvent], now: datetime) -> CalendarEvent | None:
    """The current or next event, as the calendar entity state expects."""
    live = [e for e in events if e.end_datetime_local > now]
    return min(live, key=lambda e: e.start_datetime_local) if live else None


def _between(
    events: list[CalendarEvent], start: datetime, end: datetime
) -> list[CalendarEvent]:
    return [
        e
        for e in events
        if e.start_datetime_local < end and e.end_datetime_local > start
    ]


class TimetableCalendar(EdulinkEntity[Timetable], CalendarEntity):
    """One event per lesson (registration excluded)."""

    def _events(self) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                start=lesson.start,
                end=lesson.end,
                summary=lesson.subject,
                location=lesson.room,
                description="\n".join(
                    part
                    for part in (
                        f"Period {lesson.period}",
                        lesson.group,
                        lesson.teacher,
                    )
                    if part
                ),
                uid=f"{self.learner.id}_{lesson.start.isoformat()}",
            )
            for day in self.learner_data.days.values()
            for lesson in day.teaching
        ]

    @property
    def event(self) -> CalendarEvent | None:
        """Current or next lesson."""
        return _upcoming(self._events(), school_now())

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Lessons in a range."""
        return _between(self._events(), start_date, end_date)


class HomeworkCalendar(EdulinkEntity[HomeworkList], CalendarEntity):
    """All-day events on homework due dates."""

    def _events(self) -> list[CalendarEvent]:
        data = self.learner_data
        return [
            CalendarEvent(
                start=hw.due,
                end=hw.due + timedelta(days=1),
                summary=f"{hw.subject}: {hw.activity}",
                description="\n".join(
                    part
                    for part in (hw.description, f"Set by {hw.set_by}", hw.status)
                    if part
                ),
                uid=f"{self.learner.id}_{hw.id}",
            )
            for hw in (*data.current, *data.past)
        ]

    @property
    def event(self) -> CalendarEvent | None:
        """Next homework due."""
        return _upcoming(self._events(), school_now())

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Homework due in a range."""
        return _between(self._events(), start_date, end_date)
