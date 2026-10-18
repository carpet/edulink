"""Tests for multi-learner data, failure handling, the clock and polling."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.edulink.const import (
    CONF_ACADEMIC_YEAR_START,
    DOMAIN,
    OFF_HOURS_INTERVAL,
    RECORD_INTERVAL,
    RETRY_INTERVAL,
    STATUS_INTERVAL,
)
from custom_components.edulink.models import SCHOOL_TZ

from .conftest import NOW, FakeEdulink, load

NOT_AUTHORISED = "You are not authorised to carry out this action"

# Every test talks to the fake server, never the network.
pytestmark = pytest.mark.usefixtures("fake")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    st = hass.states.get(entity_id)
    assert st is not None, entity_id
    return st.state


def at(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=SCHOOL_TZ)


@pytest.fixture
def sam_catering(fake: FakeEdulink) -> None:
    """Give the second child a different catering account."""
    other = load("catering.json")
    other["result"]["balance"] = 3
    other["result"]["transactions"] = []
    fake.learner_responses[("EduLink.Catering", "7002")] = other


@pytest.mark.usefixtures("sam_catering")
async def test_two_learners_get_their_own_data(
    hass: HomeAssistant,
    fake: FakeEdulink,
    two_learner_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each child's entities show that child's data."""
    freezer.move_to(NOW)
    await _setup(hass, two_learner_entry)
    assert _state(hass, "sensor.alex_catering_balance") == "11.0"
    assert _state(hass, "sensor.sam_catering_balance") == "3.0"
    assert _state(hass, "binary_sensor.sam_low_catering_balance") == "on"
    assert _state(hass, "sensor.sam_spent_this_week") == "0"
    learners = {p.get("learner_id") for m, p in fake.calls if m == "EduLink.Catering"}
    assert learners == {"7001", "7002"}


async def test_two_learners_get_their_own_clubs(
    hass: HomeAssistant,
    fake: FakeEdulink,
    two_learner_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Club sessions are per child; status entities are created once per account."""
    freezer.move_to(NOW)
    fake.learner_responses[("EduLink.Clubs", "7002")] = {
        "result": {"success": True, "clubs": []}
    }
    await _setup(hass, two_learner_entry)
    assert _state(hass, "sensor.alex_next_club_session") == "2026-10-20T14:15:00+00:00"
    assert _state(hass, "sensor.sam_next_club_session") == "unknown"
    assert fake.methods().count("EduLink.Status") == 1
    assert hass.states.get("sensor.edulink_one_new_messages_2") is None


async def test_one_learner_failing_does_not_block_the_other(
    hass: HomeAssistant,
    fake: FakeEdulink,
    two_learner_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A permission error for one child leaves the other working."""
    freezer.move_to(NOW)
    fake.learner_errors[("EduLink.Catering", "7002")] = NOT_AUTHORISED
    await _setup(hass, two_learner_entry)
    assert two_learner_entry.state is ConfigEntryState.LOADED
    assert _state(hass, "sensor.alex_catering_balance") == "11.0"
    assert _state(hass, "sensor.sam_catering_balance") == "unavailable"
    catering = two_learner_entry.runtime_data.catering
    assert catering.last_update_success
    assert catering.update_interval == RETRY_INTERVAL


async def test_failing_learner_keeps_last_good_data(
    hass: HomeAssistant,
    fake: FakeEdulink,
    two_learner_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A learner that worked before keeps its last values through a failure."""
    freezer.move_to(NOW)
    await _setup(hass, two_learner_entry)
    fake.learner_errors[("EduLink.Catering", "7002")] = "Temporary failure"
    await two_learner_entry.runtime_data.catering.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "sensor.sam_catering_balance") == "11.0"


async def test_failed_timetable_refresh_keeps_entities_available(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The cached timetable still covers today, so wake-up automations keep working.

    Catering has no such cache guarantee and goes unavailable.
    """
    freezer.move_to(NOW)
    await _setup(hass, entry)
    fake.network_errors.update({"EduLink.Timetable", "EduLink.Catering"})
    runtime = entry.runtime_data
    await runtime.timetable.async_refresh()
    await runtime.catering.async_refresh()
    await hass.async_block_till_done()

    assert not runtime.timetable.last_update_success
    assert runtime.timetable.update_interval == RETRY_INTERVAL
    assert _state(hass, "binary_sensor.alex_school_today") == "on"
    assert _state(hass, "sensor.alex_current_lesson") == "English"
    assert _state(hass, "sensor.alex_catering_balance") == "unavailable"


async def test_setup_failure_logs_out(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed first refresh closes the session before setup is retried."""
    freezer.move_to(NOW)
    fake.network_errors.add("EduLink.Catering")
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fake.methods()[-1] == "EduLink.Logout"
    assert not fake.logged_in


async def test_midnight_rollover(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Date-based homework and catering states roll over at midnight without polling."""
    freezer.move_to("2026-10-16 23:59:00+01:00")
    await _setup(hass, entry)
    assert _state(hass, "sensor.alex_homework_due_tomorrow") == "2"
    assert _state(hass, "sensor.alex_spent_today") == "2.8"
    homework_polls = fake.methods().count("EduLink.Homework")
    timetable_polls = fake.methods().count("EduLink.Timetable")

    freezer.move_to("2026-10-17 00:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, "sensor.alex_homework_due_today") == "2"
    assert _state(hass, "sensor.alex_homework_due_tomorrow") == "0"
    assert _state(hass, "sensor.alex_spent_today") == "0"
    assert (
        hass.states.get("sensor.alex_spent_today").attributes["last_reset"]
        == "2026-10-17T00:00:00+01:00"
    )
    assert fake.methods().count("EduLink.Homework") == homework_polls
    assert fake.methods().count("EduLink.Timetable") == timetable_polls + 1


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_weekly_totals_roll_over(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Achievements this week reset at Monday midnight without polling."""
    freezer.move_to("2026-10-18 23:59:00+01:00")
    await _setup(hass, entry)
    assert _state(hass, "sensor.alex_achievements_this_week") == "2"
    polls = fake.methods().count("EduLink.Achievement")

    freezer.move_to("2026-10-19 00:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    week = hass.states.get("sensor.alex_achievements_this_week")
    assert week.state == "0"
    assert week.attributes["last_reset"] == "2026-10-19T00:00:00+01:00"
    assert _state(hass, "sensor.alex_achievement_points_this_year") == "10"
    assert fake.methods().count("EduLink.Achievement") == polls


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_yearly_totals_roll_over(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Points and behaviour events this year reset on 1 September."""
    freezer.move_to("2027-08-31 23:59:00+01:00")
    await _setup(hass, entry)
    # Since 1 September 2026: 3 + 2 + 5 points; two behaviour events.
    assert _state(hass, "sensor.alex_achievement_points_this_year") == "10"
    assert _state(hass, "sensor.alex_behaviour_events_this_year") == "2"

    freezer.move_to("2027-09-01 00:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    points = hass.states.get("sensor.alex_achievement_points_this_year")
    assert points.state == "0"
    assert points.attributes["last_reset"] == "2027-09-01T00:00:00+01:00"
    events = hass.states.get("sensor.alex_behaviour_events_this_year")
    assert events.state == "0"
    assert events.attributes["last_reset"] == "2027-09-01T00:00:00+01:00"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_yearly_totals_use_start_month_option(
    hass: HomeAssistant,
    entry_data: dict,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A January start month resets the year totals on 1 January."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="999_5001",
        data=entry_data,
        options={CONF_ACADEMIC_YEAR_START: 1},
        title="Example",
    )
    freezer.move_to("2026-12-31 23:59:00+00:00")
    await _setup(hass, entry)
    # Every fixture event is in 2026: 3 + 2 + 5 + 10 points, three events.
    assert _state(hass, "sensor.alex_achievement_points_this_year") == "20"
    assert _state(hass, "sensor.alex_behaviour_events_this_year") == "3"

    freezer.move_to("2027-01-01 00:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    points = hass.states.get("sensor.alex_achievement_points_this_year")
    assert points.state == "0"
    assert points.attributes["last_reset"] == "2027-01-01T00:00:00+00:00"
    assert _state(hass, "sensor.alex_behaviour_events_this_year") == "0"


async def test_next_club_session_moves_on_when_a_session_starts(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Once a session starts the next one shows, without polling Edulink."""
    clubs = load("clubs.json")
    clubs["result"]["clubs"][1]["next_session"] = "2026-10-16 10:30:00"
    fake.responses["EduLink.Clubs"] = clubs
    freezer.move_to(NOW)
    await _setup(hass, entry)
    session = hass.states.get("sensor.alex_next_club_session")
    assert session.state == "2026-10-16T09:30:00+00:00"
    assert session.attributes["club"] == "Choir"
    polls = fake.methods().count("EduLink.Clubs")

    freezer.move_to("2026-10-16 10:31:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    session = hass.states.get("sensor.alex_next_club_session")
    assert session.state == "2026-10-20T14:15:00+00:00"
    assert session.attributes["club"] == "Chess Club"
    assert fake.methods().count("EduLink.Clubs") == polls


async def test_slow_module_intervals(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """School record and clubs poll twice a day; status every half hour."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    runtime = entry.runtime_data
    for coordinator in (
        runtime.achievement,
        runtime.behaviour,
        runtime.attendance,
        runtime.clubs,
    ):
        assert coordinator.update_interval == RECORD_INTERVAL
    assert runtime.status.update_interval == STATUS_INTERVAL


async def test_calendar_ignores_clock_ticks(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """The minute tick updates sensors but does not rewrite calendars."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    calendar_before = hass.states.get("calendar.alex_timetable").last_reported
    sensor_before = hass.states.get("sensor.alex_current_lesson").last_reported

    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.alex_current_lesson").last_reported > sensor_before
    assert hass.states.get("calendar.alex_timetable").last_reported == calendar_before


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(10, 16, 10), timedelta(minutes=15)),  # school day, open
        (at(10, 16, 15, 55), timedelta(minutes=5)),  # capped at closing
        (at(10, 16, 16, 30), OFF_HOURS_INTERVAL),  # Friday after school
        (at(10, 19, 6), timedelta(minutes=90)),  # wake at Monday opening
        (at(10, 19, 7, 20), timedelta(minutes=10)),
        (at(10, 26, 7, 20), OFF_HOURS_INTERVAL),  # half-term Monday
        (at(10, 26, 10), OFF_HOURS_INTERVAL),
        (at(11, 2, 6, 30), timedelta(hours=1)),  # back after half-term
    ],
)
async def test_catering_interval(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    now: datetime,
    expected: timedelta,
) -> None:
    """Fast polling only inside school hours on school days, starting on time."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    assert entry.runtime_data.catering._interval(now) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(10, 16, 10), timedelta(minutes=30)),
        (at(10, 16, 21, 50), timedelta(minutes=10)),
        (at(10, 16, 23), OFF_HOURS_INTERVAL),
        (at(10, 17, 5), timedelta(hours=1)),
    ],
)
async def test_homework_interval(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    now: datetime,
    expected: timedelta,
) -> None:
    """Homework slows overnight and speeds up again at 06:00."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    assert entry.runtime_data.homework._interval(now) == expected


async def test_calendar_events_in_range(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Calendar queries return only events overlapping the range."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": ["calendar.alex_timetable", "calendar.alex_homework"],
            "start_date_time": "2026-10-19 00:00:00+01:00",
            "end_date_time": "2026-10-19 10:00:00+01:00",
        },
        blocking=True,
        return_response=True,
    )
    lessons = result["calendar.alex_timetable"]["events"]
    assert [(e["summary"], e["location"]) for e in lessons] == [
        ("Mathematics", "R1"),
        ("English", "R2"),
    ]
    assert "Dr Teacher1" in lessons[0]["description"]
    assert result["calendar.alex_homework"]["events"] == []

    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.alex_homework",
            "start_date_time": "2026-10-17 00:00:00+01:00",
            "end_date_time": "2026-10-18 00:00:00+01:00",
        },
        blocking=True,
        return_response=True,
    )
    events = result["calendar.alex_homework"]["events"]
    assert sorted(e["summary"] for e in events) == [
        "Mathematics: Mathematics task 102",
        "Science: Science task 103",
    ]
    assert events[0]["start"] == "2026-10-17"
    assert events[0]["end"] == "2026-10-18"


async def test_todo_items(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """To-do items carry status, due date and description."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": "todo.alex_homework"},
        blocking=True,
        return_response=True,
    )
    items = result["todo.alex_homework"]["items"]
    assert [(i["uid"], i["status"], i["due"]) for i in items] == [
        ("101", "needs_action", "2026-10-16"),
        ("102", "needs_action", "2026-10-17"),
        ("103", "needs_action", "2026-10-17"),
        ("104", "completed", "2026-10-21"),
        ("105", "needs_action", "2027-02-14"),
    ]
    assert items[0]["summary"] == "English: English task 101"
    assert items[0]["description"] == "Read chapter 3\nAnswer questions 1-4"


async def test_homework_description_cache_is_pruned(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Descriptions of homework that is no longer current are forgotten."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    homework = entry.runtime_data.homework
    assert len(homework._descriptions) == 5
    trimmed = copy.deepcopy(fake.responses["EduLink.Homework"])
    trimmed["result"]["homework"]["current"] = trimmed["result"]["homework"]["current"][
        :2
    ]
    fake.responses["EduLink.Homework"] = trimmed
    await homework.async_refresh()
    assert {key[2] for key in homework._descriptions} == {"101", "102"}


async def test_reauth_reloads_once_without_deprecation(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reauth on a loaded entry reloads it exactly once."""
    freezer.move_to(NOW)
    await _setup(hass, entry)
    fake.calls.clear()
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "p_example", "password": "new"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED
    # One validation login, one login for the single reload.
    assert fake.methods().count("EduLink.Login") == 2
    assert "update listener" not in caplog.text
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
