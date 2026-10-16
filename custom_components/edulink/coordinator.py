"""Data update coordinators for Edulink One."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
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


class EdulinkCoordinator[T](DataUpdateCoordinator[dict[str, T]]):
    """Fetches one module for every selected learner, keyed by learner id."""

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
        try:
            for learner in self.learners:
                data[learner.id] = await self._fetch(learner.id)
        except EdulinkAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EdulinkError as err:
            raise UpdateFailed(str(err)) from err
        self._adjust_interval(school_now())
        return data

    @abstractmethod
    async def _fetch(self, learner_id: str) -> T:
        """Fetch and parse data for one learner."""

    def _adjust_interval(self, now: datetime) -> None:
        """Hook for coordinators that poll less often out of hours."""


class TimetableCoordinator(EdulinkCoordinator[Timetable]):
    """Timetable for the next few weeks.

    The data changes rarely; time-dependent entities (current lesson etc.)
    are recalculated every minute from the cached copy.
    """

    def __init__(self, hass, entry, client, learners) -> None:
        """Initialise the coordinator."""
        super().__init__(hass, entry, client, learners, "timetable", TIMETABLE_INTERVAL)

    async def _fetch(self, learner_id: str) -> Timetable:
        return parse_timetable(
            await self.client.timetable(learner_id, school_now().date())
        )

    @callback
    def async_start_clock(self) -> None:
        """Refresh entity states each minute and the data shortly after midnight."""

        @callback
        def _tick(_now: datetime) -> None:
            if self.data is not None:
                self.async_update_listeners()

        async def _midnight(_now: datetime) -> None:
            await self.async_request_refresh()

        entry = self.config_entry
        entry.async_on_unload(
            async_track_time_interval(
                self.hass, _tick, timedelta(minutes=1), cancel_on_shutdown=True
            )
        )
        entry.async_on_unload(
            async_track_time_change(self.hass, _midnight, hour=0, minute=5, second=0)
        )


class HomeworkCoordinator(EdulinkCoordinator[HomeworkList]):
    """Current and recent homework, with descriptions for current items."""

    def __init__(self, hass, entry, client, learners) -> None:
        """Initialise the coordinator."""
        self._fast = timedelta(
            minutes=entry.options.get(CONF_HOMEWORK_INTERVAL, DEFAULT_HOMEWORK_INTERVAL)
        )
        super().__init__(hass, entry, client, learners, "homework", self._fast)
        self._descriptions: dict[tuple[str, str], str | None] = {}

    async def _fetch(self, learner_id: str) -> HomeworkList:
        homework = parse_homework(
            await self.client.homework(learner_id),
            school_now().date(),
            PAST_HOMEWORK_DAYS,
        )
        for hw in homework.current:
            key = (learner_id, hw.id)
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
                self._descriptions[key] = html_to_text(
                    (details.get("homework") or {}).get("description")
                )
            hw.description = self._descriptions.get(key)
        return homework

    def _adjust_interval(self, now: datetime) -> None:
        overnight = now.time() >= time(22) or now.time() < time(6)
        self.update_interval = OFF_HOURS_INTERVAL if overnight else self._fast


class CateringCoordinator(EdulinkCoordinator[Catering]):
    """Catering balance and transactions. Polls faster during the school day."""

    def __init__(self, hass, entry, client, learners) -> None:
        """Initialise the coordinator."""
        self._fast = timedelta(
            minutes=entry.options.get(CONF_CATERING_INTERVAL, DEFAULT_CATERING_INTERVAL)
        )
        super().__init__(hass, entry, client, learners, "catering", self._fast)

    async def _fetch(self, learner_id: str) -> Catering:
        return parse_catering(await self.client.catering(learner_id))

    def _adjust_interval(self, now: datetime) -> None:
        school_hours = now.weekday() < 5 and time(7, 30) <= now.time() < time(16)
        self.update_interval = self._fast if school_hours else OFF_HOURS_INTERVAL
