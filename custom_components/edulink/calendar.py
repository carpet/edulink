"""Calendars for Edulink One: lessons and homework due dates."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant, callback
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


class _EdulinkCalendar[T](EdulinkEntity[T], CalendarEntity):
    """Builds events once per data update.

    The calendar platform schedules its own state changes at event start and
    end, so updates that bring no new data (the per-minute clock) are ignored.
    """

    _built_for: T | None = None
    _built: list[CalendarEvent]
    _written: tuple[dict[str, T] | None, bool] | None = None

    def _build(self, data: T) -> list[CalendarEvent]:
        raise NotImplementedError

    def _events(self) -> list[CalendarEvent]:
        data = self.learner_data
        if data is not self._built_for:
            self._built = self._build(data)
            self._built_for = data
        return self._built

    async def async_added_to_hass(self) -> None:
        """Remember what the first write was based on."""
        await super().async_added_to_hass()
        self._written = (self.coordinator.data, self.available)

    @callback
    def _handle_coordinator_update(self) -> None:
        written = (self.coordinator.data, self.available)
        if self._written and (
            written[0] is self._written[0] and written[1] == self._written[1]
        ):
            return
        self._written = written
        super()._handle_coordinator_update()

    @property
    def event(self) -> CalendarEvent | None:
        """Current or next event."""
        return _upcoming(self._events(), school_now())

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping a range."""
        return [
            e
            for e in self._events()
            if e.start_datetime_local < end_date and e.end_datetime_local > start_date
        ]


class TimetableCalendar(_EdulinkCalendar[Timetable]):
    """One event per lesson (registration excluded)."""

    def _build(self, data: Timetable) -> list[CalendarEvent]:
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
            for day in data.days.values()
            for lesson in day.teaching
        ]


class HomeworkCalendar(_EdulinkCalendar[HomeworkList]):
    """All-day events on homework due dates."""

    def _build(self, data: HomeworkList) -> list[CalendarEvent]:
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
