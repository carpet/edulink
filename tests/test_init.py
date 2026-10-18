"""Tests for setup, entities and error handling."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.edulink.const import (
    CONF_LEARNERS,
    CONF_USER_ID,
    DOMAIN,
    RETRY_INTERVAL,
)
from custom_components.edulink.diagnostics import async_get_config_entry_diagnostics

from .conftest import LOGIN_FAILED, NOW, FakeEdulink, load


@pytest.fixture
async def setup(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """Set up the integration at the fixed test time."""
    freezer.move_to(NOW)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def state(hass: HomeAssistant, entity_id: str):
    st = hass.states.get(entity_id)
    assert st is not None, entity_id
    return st


async def test_learner_login_keeps_account_device_separate(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry_data: dict,
    freezer: FrozenDateTimeFactory,
) -> None:
    """On a learner's own login the account entities get their own device."""
    login = load("login.json")
    login["result"]["user"] = {
        **login["result"]["user"],
        "id": "7001",
        "forename": "Alex",
        "types": ["learner"],
    }
    login["result"]["children"] = []
    fake.responses["EduLink.Login"] = login
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="999_7001",
        data={**entry_data, CONF_USER_ID: "7001", CONF_LEARNERS: ["7001"]},
        title="Example",
    )
    freezer.move_to(NOW)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    learner = device_registry.async_get_device_by_identifier(
        (DOMAIN, "999_7001"), entry.entry_id
    )
    account = device_registry.async_get_device_by_identifier(
        (DOMAIN, "999_7001_account"), entry.entry_id
    )
    assert learner.name == "Alex"
    assert account.name == "Edulink One"
    lesson = entity_registry.async_get("sensor.alex_current_lesson")
    messages = entity_registry.async_get("sensor.edulink_one_new_messages")
    assert lesson.device_id == learner.id
    assert messages.device_id == account.id


async def test_old_account_device_is_replaced(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The pre-v0.1.6 account device goes; its entities keep their ids."""
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    old = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "999_5001")},
        name="Edulink One",
    )
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "999_5001_new_messages",
        config_entry=entry,
        device_id=old.id,
        suggested_object_id="my_messages",
    )
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get(old.id) is None
    new = device_registry.async_get_device_by_identifier(
        (DOMAIN, "999_5001_account"), entry.entry_id
    )
    messages = entity_registry.async_get("sensor.my_messages")
    assert messages.device_id == new.id
    assert state(hass, "sensor.my_messages").state == "2"


async def test_setup_creates_device(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """Only the selected child gets a device; learner_id is always sent."""
    assert setup.state is ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup.entry_id)
    assert sorted(d.name for d in devices) == ["Alex", "Edulink One"]
    assert {d.model for d in devices} == {"Example Academy"}
    learner_methods = {
        "EduLink.Timetable",
        "EduLink.Homework",
        "EduLink.HomeworkDetails",
        "EduLink.Catering",
        "EduLink.Achievement",
        "EduLink.Behaviour",
        "EduLink.Attendance",
        "EduLink.Clubs",
    }
    assert learner_methods <= set(fake.methods())
    for method, params in fake.calls:
        if method in learner_methods:
            assert params["learner_id"] == "7001", method
    assert ("EduLink.Status", {"last_visible": 0}) in fake.calls


async def test_timetable_entities(
    hass: HomeAssistant, setup: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Timetable sensors at Friday 10:00, then after the clock ticks."""
    current = state(hass, "sensor.alex_current_lesson")
    assert current.state == "English"
    assert current.attributes["room"] == "R2"
    assert state(hass, "sensor.alex_next_lesson").state == "Drama"
    assert (
        state(hass, "sensor.alex_school_day_start").state == "2026-10-16T07:30:00+00:00"
    )
    assert (
        state(hass, "sensor.alex_school_day_end").state == "2026-10-16T14:00:00+00:00"
    )
    assert state(hass, "sensor.alex_next_school_day").state == "2026-10-19"
    assert state(hass, "sensor.alex_lessons_tomorrow").state == "0"
    today = state(hass, "sensor.alex_lessons_today")
    assert today.state == "5"
    assert [lesson["subject"] for lesson in today.attributes["lessons"]] == [
        "Mathematics",
        "English",
        "Drama",
        "History",
        "Physical Education",
    ]
    kit = state(hass, "sensor.alex_kit_today")
    assert kit.state == "1"
    assert kit.attributes["items"] == ["PE kit"]
    assert kit.attributes["date"] == "2026-10-16"
    kit_next = state(hass, "sensor.alex_kit_for_next_school_day")
    assert kit_next.state == "1"
    assert kit_next.attributes["date"] == "2026-10-19"
    assert state(hass, "sensor.alex_timetable_week").state == "A"
    assert state(hass, "binary_sensor.alex_school_today").state == "on"
    assert state(hass, "binary_sensor.alex_school_tomorrow").state == "off"
    assert state(hass, "calendar.alex_timetable").attributes["message"] == "English"

    # The per-minute clock moves current lesson on without an API call.
    freezer.tick(timedelta(minutes=55))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, "sensor.alex_current_lesson").state == "Break"

    # After school, start/end and week roll to Monday (week B).
    freezer.move_to("2026-10-16 16:00:00+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, "sensor.alex_current_lesson").state == "Not in school"
    assert (
        state(hass, "sensor.alex_school_day_start").state == "2026-10-19T07:30:00+00:00"
    )
    assert state(hass, "sensor.alex_timetable_week").state == "B"


async def test_homework_entities(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    """Homework sensors, to-do and calendar."""
    # Default look-ahead of 7 days leaves out homework due in February.
    outstanding = state(hass, "sensor.alex_homework_outstanding")
    assert outstanding.state == "3"
    assert [h["due"] for h in outstanding.attributes["homework"]] == [
        "2026-10-16",
        "2026-10-17",
        "2026-10-17",
    ]
    assert [h["days_until_due"] for h in outstanding.attributes["homework"]] == [
        0,
        1,
        1,
    ]
    urgency = state(hass, "sensor.alex_homework_urgency")
    assert urgency.state == "today"
    assert urgency.attributes["options"] == ["none", "later", "tomorrow", "today"]
    assert state(hass, "sensor.alex_homework_due_today").state == "1"
    assert state(hass, "sensor.alex_homework_due_tomorrow").state == "2"
    nxt = state(hass, "sensor.alex_next_homework_due")
    assert nxt.state == "2026-10-16"
    assert nxt.attributes["description"] == "Read chapter 3\nAnswer questions 1-4"
    assert "description" in nxt.state_info["unrecorded_attributes"]
    assert state(hass, "todo.alex_homework").state == "4"
    assert (
        state(hass, "calendar.alex_homework").attributes["message"]
        == "English: English task 101"
    )
    # Overdue is disabled by default.
    assert hass.states.get("sensor.alex_overdue_homework") is None
    assert er.async_get(hass).async_get("sensor.alex_overdue_homework").disabled


async def test_homework_details_cached(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """Descriptions are fetched once per homework item."""
    first = fake.methods().count("EduLink.HomeworkDetails")
    assert first == 5
    await setup.runtime_data.homework.async_refresh()
    assert fake.methods().count("EduLink.HomeworkDetails") == first


async def test_catering_entities(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    """Catering sensors."""
    balance = state(hass, "sensor.alex_catering_balance")
    assert balance.state == "11.0"
    assert balance.attributes["unit_of_measurement"] == "GBP"
    assert state(hass, "binary_sensor.alex_low_catering_balance").state == "off"
    today = state(hass, "sensor.alex_spent_today")
    assert today.state == "2.8"
    assert today.attributes["items"] == ["Pizza Slice", "Flavoured Water"]
    assert today.attributes["state_class"] == "total"
    assert today.attributes["last_reset"] == "2026-10-16T00:00:00+01:00"
    week = state(hass, "sensor.alex_spent_this_week")
    assert week.state == "6.2"
    assert week.attributes["state_class"] == "total"
    assert week.attributes["last_reset"] == "2026-10-12T00:00:00+01:00"
    assert "last_reset" not in balance.attributes
    assert state(hass, "sensor.alex_last_purchase").state == "2026-10-16T11:34:38+00:00"
    top_up = state(hass, "sensor.alex_last_top_up")
    assert top_up.state == "2026-10-15T07:00:00+00:00"
    assert top_up.attributes["amount"] == 15
    amount = state(hass, "sensor.alex_last_top_up_amount")
    assert amount.state == "15.0"
    assert amount.attributes["unit_of_measurement"] == "GBP"
    assert "state_class" not in amount.attributes


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_school_record_entities(
    hass: HomeAssistant, setup: MockConfigEntry
) -> None:
    """Achievement, behaviour and attendance: counts, points and dates only."""
    points = state(hass, "sensor.alex_achievement_points_this_year")
    assert points.state == "10"
    assert points.attributes["state_class"] == "total"
    assert points.attributes["last_reset"] == "2026-09-01T00:00:00+01:00"
    week = state(hass, "sensor.alex_achievements_this_week")
    assert week.state == "2"
    assert week.attributes["last_reset"] == "2026-10-12T00:00:00+01:00"
    assert state(hass, "sensor.alex_last_achievement").state == "2026-10-15"
    assert state(hass, "sensor.alex_behaviour_events_this_year").state == "2"
    assert state(hass, "sensor.alex_last_behaviour_event").state == "2026-10-14"
    assert state(hass, "sensor.alex_last_absence").state == "2026-10-05"
    for st in hass.states.async_all("sensor"):
        text = repr(st.attributes)
        for private in ("Synthetic", "Member1", "10A/"):
            assert private not in text, (st.entity_id, private)


async def test_school_record_entities_disabled_by_default(
    hass: HomeAssistant, setup: MockConfigEntry
) -> None:
    """Nothing about a child's record is recorded until someone opts in."""
    registry = er.async_get(hass)
    for entity_id in (
        "sensor.alex_achievement_points_this_year",
        "sensor.alex_achievements_this_week",
        "sensor.alex_last_achievement",
        "sensor.alex_behaviour_events_this_year",
        "sensor.alex_last_behaviour_event",
        "sensor.alex_last_absence",
    ):
        assert hass.states.get(entity_id) is None, entity_id
        assert registry.async_get(entity_id).disabled, entity_id


async def test_club_and_status_entities(
    hass: HomeAssistant, setup: MockConfigEntry
) -> None:
    """Next club session per child; unread counts on the account device."""
    club = state(hass, "sensor.alex_next_club_session")
    assert club.state == "2026-10-20T14:15:00+00:00"
    assert club.attributes["club"] == "Chess Club"
    assert club.attributes["clubs"] == ["Chess Club", "Choir"]
    assert club.attributes["sessions"] == [
        {
            "club": "Chess Club",
            "start": "2026-10-20T15:15:00+01:00",
            "location": "Library",
        }
    ]

    chess = state(hass, "sensor.alex_chess_club")
    assert chess.state == "2026-10-20T14:15:00+00:00"
    assert chess.attributes["location"] == "Library"
    assert chess.attributes["friendly_name"] == "Alex Chess Club"
    assert er.async_get(hass).async_get(chess.entity_id).unique_id == "999_7001_club_41"
    # Choir's session this morning has started; Edulink gives the next at the
    # next poll.
    assert state(hass, "sensor.alex_choir").state == "unknown"
    assert hass.states.get("sensor.alex_old_club") is None
    assert state(hass, "sensor.edulink_one_new_messages").state == "2"
    assert state(hass, "sensor.edulink_one_new_forms").state == "1"
    assert state(hass, "sensor.edulink_one_new_noticeboard_items").state == "3"
    entry = er.async_get(hass).async_get("sensor.edulink_one_new_messages")
    assert entry.unique_id == "999_5001_new_messages"


async def test_status_not_authorised(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An account that may not call Status still loads, without status entities."""
    freezer.move_to(NOW)
    fake.errors["EduLink.Status"] = "You are not authorised to carry out this action"
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.status is None
    assert hass.states.get("sensor.edulink_one_new_messages") is None
    assert hass.states.get("sensor.alex_next_club_session") is not None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_optional_module_failure_does_not_block_setup(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing optional module is unavailable and retried; the rest load."""
    freezer.move_to(NOW)
    fake.network_errors.update({"EduLink.Status", "EduLink.Behaviour"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor.alex_current_lesson").state == "English"
    assert state(hass, "sensor.alex_last_behaviour_event").state == "unavailable"
    assert state(hass, "sensor.edulink_one_new_messages").state == "unavailable"
    assert entry.runtime_data.behaviour.update_interval == RETRY_INTERVAL

    fake.network_errors.clear()
    freezer.tick(RETRY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, "sensor.alex_last_behaviour_event").state == "2026-10-14"
    assert state(hass, "sensor.edulink_one_new_messages").state == "2"


async def test_session_expiry_relogs_in(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """An expired session triggers one login and a retry."""
    fake.calls.clear()
    fake.expire()
    await setup.runtime_data.catering.async_refresh()
    assert fake.methods() == ["EduLink.Catering", "EduLink.Login", "EduLink.Catering"]
    assert setup.runtime_data.catering.last_update_success


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """If re-login fails, reauth starts."""
    fake.expire()
    fake.errors["EduLink.Login"] = LOGIN_FAILED
    await setup.runtime_data.catering.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_login_failure(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Bad credentials at startup."""
    fake.errors["EduLink.Login"] = LOGIN_FAILED
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_other_login_error_retries(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """A login error that is not bad credentials retries instead of reauth."""
    fake.errors["EduLink.Login"] = "Too many requests, please try again later"
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_setup_network_failure(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Edulink unreachable at startup."""
    fake.network_errors.add("EduLink.Login")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_module_gating(
    hass: HomeAssistant,
    fake: FakeEdulink,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Modules missing from the school's menu are neither polled nor created."""
    freezer.move_to(NOW)
    fake.responses["EduLink.Login"]["result"]["learner_menu"] = [{"id": "timetable"}]
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for method in (
        "EduLink.Catering",
        "EduLink.Achievement",
        "EduLink.Behaviour",
        "EduLink.Attendance",
        "EduLink.Clubs",
    ):
        assert method not in fake.methods(), method
    assert hass.states.get("sensor.alex_catering_balance") is None
    assert hass.states.get("sensor.alex_next_club_session") is None
    assert er.async_get(hass).async_get("sensor.alex_last_absence") is None
    assert hass.states.get("todo.alex_homework") is None
    assert hass.states.get("sensor.alex_current_lesson") is not None


async def test_unload_logs_out(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """Unloading ends the Edulink session."""
    assert await hass.config_entries.async_unload(setup.entry_id)
    assert fake.methods()[-1] == "EduLink.Logout"


async def test_diagnostics_have_no_personal_data(
    hass: HomeAssistant, setup: MockConfigEntry
) -> None:
    """Diagnostics carry counts only."""
    diag = await async_get_config_entry_diagnostics(hass, setup)
    text = repr(diag)
    for secret in (
        "secret",
        "p_example",
        "Alex",
        "English task",
        "PIZZA",
        "7001",
        "Chess",
        "Synthetic",
        "Member1",
    ):
        assert secret not in text
    learner = diag["learners"][0]
    assert learner["homework"]["current"] == 5
    assert learner["achievement"] == {"events": 4}
    assert learner["behaviour"] == {"events": 3}
    assert learner["attendance"] == {"absences": 2}
    assert learner["clubs"] == {"clubs": 2, "sessions": 2}
    assert diag["coordinators"]["status"]["last_update_success"]
