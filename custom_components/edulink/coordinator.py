"""Data update coordinators for Edulink One."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EdulinkAuthError, EdulinkClient, EdulinkError, Learner
from .const import (
    CONF_CATERING_INTERVAL,
    CONF_HOMEWORK_INTERVAL,
    DEFAULT_CATERING_INTERVAL,
    DEFAULT_HOMEWORK_INTERVAL,
    DOMAIN,
    OFF_HOURS_INTERVAL,
    PAST_HOMEWORK_DAYS,
    RETRY_INTERVAL,
    TIMETABLE_INTERVAL,
)
from .models import (
    SCHOOL_TZ,
    Catering,
    HomeworkList,
    Timetable,
    html_to_text,
    parse_catering,
    parse_homework,
    parse_timetable,
)

_LOGGER = logging.getLogger(__name__)

CATERING_OPENS = time(7, 30)
CATERING_CLOSES = time(16)
HOMEWORK_NIGHT_STARTS = time(22)
HOMEWORK_NIGHT_ENDS = time(6)

type EdulinkConfigEntry = ConfigEntry[EdulinkRuntimeData]


@dataclass
class EdulinkRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: EdulinkClient
    school_name: str
    establishment_id: str
    learners: list[Learner]
    timetable: TimetableCoordinator | None = None
    homework: HomeworkCoordinator | None = None
    catering: CateringCoordinator | None = None


def school_now() -> datetime:
    """Current time in the school's timezone."""
    return dt_util.now(SCHOOL_TZ)


def until(now: datetime, at: time) -> timedelta:
    """Time from now until the next occurrence of a school-local time of day."""
    target = datetime.combine(now.date(), at, SCHOOL_TZ)
    if target <= now:
        target = datetime.combine(now.date() + timedelta(days=1), at, SCHOOL_TZ)
    return target - now


class EdulinkCoordinator[T](DataUpdateCoordinator[dict[str, T]]):
    """Fetches one module for every selected learner, keyed by learner id.

    A failure for one learner keeps that learner's previous data and does not
    affect the others. The update only fails if every learner fails.
    """

    config_entry: EdulinkConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EdulinkConfigEntry,
        client: EdulinkClient,
        learners: list[Learner],
        name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=update_interval,
        )
        self.client = client
        self.learners = learners

    async def _async_update_data(self) -> dict[str, T]:
        data: dict[str, T] = {}
        errors: list[EdulinkError] = []
        for learner in self.learners:
            try:
                data[learner.id] = await self._fetch(learner.id)
            except EdulinkAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except EdulinkError as err:
                errors.append(err)
                if self.data and learner.id in self.data:
                    data[learner.id] = self.data[learner.id]

        self.update_interval = self._interval(school_now())
        if errors:
            self.update_interval = min(self.update_interval, RETRY_INTERVAL)
            if len(errors) == len(self.learners):
                raise UpdateFailed(str(errors[0]))
            _LOGGER.warning(
                "%s failed for %d of %d learners: %s",
                self.name,
                len(errors),
                len(self.learners),
                errors[0],
            )
        return data

    @abstractmethod
    async def _fetch(self, learner_id: str) -> T:
        """Fetch and parse data for one learner."""

    @abstractmethod
    def _interval(self, now: datetime) -> timedelta:
        """Delay until the next poll."""

    def is_usable(self, learner_id: str) -> bool:
        """Whether entities for a learner should be available."""
        return self.last_update_success and learner_id in (self.data or {})


class TimetableCoordinator(EdulinkCoordinator[Timetable]):
    """Timetable for the next few weeks.

    The data changes rarely, so a failed refresh does not make entities
    unavailable while the cached copy still covers today.
    """

    def __init__(self, hass, entry, client, learners) -> None:
        """Initialise the coordinator."""
        super().__init__(hass, entry, client, learners, "timetable", TIMETABLE_INTERVAL)

    async def _fetch(self, learner_id: str) -> Timetable:
        today = school_now().date()
        return parse_timetable(await self.client.timetable(learner_id, today), today)

    def _interval(self, now: datetime) -> timedelta:
        return TIMETABLE_INTERVAL

    def is_usable(self, learner_id: str) -> bool:
        """Available while the cached timetable covers today."""
        timetable = (self.data or {}).get(learner_id)
        return timetable is not None and timetable.covers(school_now().date())

    def is_school_day(self, day: date) -> bool | None:
        """True or False if any learner's timetable says so, None if unknown."""
        answers = [tt.is_school_day(day) for tt in (self.data or {}).values()]
        if any(answers):
            return True
        if answers and all(answer is False for answer in answers):
            return False
        return None


class HomeworkCoordinator(EdulinkCoordinator[HomeworkList]):
    """Current and recent homework, with descriptions for current items."""

    def __init__(self, hass, entry, client, learners) -> None:
        """Initialise the coordinator."""
        self._fast = timedelta(
            minutes=entry.options.get(CONF_HOMEWORK_INTERVAL, DEFAULT_HOMEWORK_INTERVAL)
        )
        super().__init__(hass, entry, client, learners, "homework", self._fast)
        self._descriptions: dict[tuple[str, str, str], str | None] = {}

    async def _fetch(self, learner_id: str) -> HomeworkList:
        homework = parse_homework(
            await self.client.homework(learner_id),
            school_now().date(),
            PAST_HOMEWORK_DAYS,
        )
        current: set[tuple[str, str, str]] = set()
        for hw in homework.current:
            key = (learner_id, hw.source, hw.id)
            current.add(key)
            if key not in self._descriptions:
                try:
                    details = await self.client.homework_details(
                        learner_id, hw.id, hw.source
                    )
                except EdulinkAuthError:
                    raise
                except EdulinkError as err:
                    _LOGGER.debug("No details for homework %s: %s", hw.id, err)
                    continue
                homework_details = details.get("homework")
                self._descriptions[key] = html_to_text(
                    homework_details.get("description")
                    if isinstance(homework_details, dict)
                    else None
                )
            hw.description = self._descriptions.get(key)
        # Forget homework that is no longer current.
        self._descriptions = {
            key: text
            for key, text in self._descriptions.items()
            if key[0] != learner_id or key in current
        }
        return homework

    def _interval(self, now: datetime) -> timedelta:
        if HOMEWORK_NIGHT_ENDS <= now.time() < HOMEWORK_NIGHT_STARTS:
            return min(self._fast, until(now, HOMEWORK_NIGHT_STARTS))
        return min(OFF_HOURS_INTERVAL, until(now, HOMEWORK_NIGHT_ENDS))


class CateringCoordinator(EdulinkCoordinator[Catering]):
    """Catering balance and transactions. Polls faster during the school day."""

    def __init__(
        self, hass, entry, client, learners, timetable: TimetableCoordinator | None
    ) -> None:
        """Initialise the coordinator."""
        self._fast = timedelta(
            minutes=entry.options.get(CONF_CATERING_INTERVAL, DEFAULT_CATERING_INTERVAL)
        )
        self._timetable = timetable
        super().__init__(hass, entry, client, learners, "catering", self._fast)

    async def _fetch(self, learner_id: str) -> Catering:
        return parse_catering(await self.client.catering(learner_id))

    def _is_school_day(self, day: date) -> bool:
        if self._timetable is not None:
            known = self._timetable.is_school_day(day)
            if known is not None:
                return known
        return day.weekday() < 5

    def _interval(self, now: datetime) -> timedelta:
        if self._is_school_day(now.date()) and (
            CATERING_OPENS <= now.time() < CATERING_CLOSES
        ):
            return min(self._fast, until(now, CATERING_CLOSES))
        # Sleep until the next school day opens, waking at least every OFF_HOURS.
        for offset in range(15):
            day = now.date() + timedelta(days=offset)
            opens = datetime.combine(day, CATERING_OPENS, SCHOOL_TZ)
            if opens > now and self._is_school_day(day):
                return min(OFF_HOURS_INTERVAL, opens - now)
        return OFF_HOURS_INTERVAL


@callback
def async_start_clock(
    hass: HomeAssistant, entry: EdulinkConfigEntry, runtime: EdulinkRuntimeData
) -> None:
    """Recalculate time-dependent states on the minute, and roll over at midnight.

    Midnight is detected in school time: date-based homework and catering
    sensors are recalculated and the timetable is refetched.
    """
    last_date = school_now().date()

    @callback
    def _tick(_now: datetime) -> None:
        nonlocal last_date
        today = school_now().date()
        new_day = today != last_date
        last_date = today
        for coordinator in (runtime.timetable, runtime.homework, runtime.catering):
            if coordinator is None or coordinator.data is None:
                continue
            if coordinator is runtime.timetable or new_day:
                coordinator.async_update_listeners()
        if new_day and runtime.timetable is not None:
            entry.async_create_background_task(
                hass,
                runtime.timetable.async_request_refresh(),
                "edulink timetable midnight refresh",
            )

    entry.async_on_unload(async_track_time_change(hass, _tick, second=0))
