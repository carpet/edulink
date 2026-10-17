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

from custom_components.edulink.const import DOMAIN
from custom_components.edulink.diagnostics import async_get_config_entry_diagnostics

from .conftest import NOW, FakeEdulink


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


async def test_setup_creates_device(
    hass: HomeAssistant, setup: MockConfigEntry, fake: FakeEdulink
) -> None:
    """Only the selected child gets a device; learner_id is always sent."""
    assert setup.state is ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup.entry_id)
    assert [d.name for d in devices] == ["Alex"]
    assert devices[0].model == "Example Academy"
    for method, params in fake.calls:
        if method in {
            "EduLink.Timetable",
            "EduLink.Homework",
            "EduLink.HomeworkDetails",
            "EduLink.Catering",
        }:
            assert params["learner_id"] == "7001", method


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
    outstanding = state(hass, "sensor.alex_homework_outstanding")
    assert outstanding.state == "4"
    assert len(outstanding.attributes["homework"]) == 4
    assert state(hass, "sensor.alex_homework_due_today").state == "1"
    assert state(hass, "sensor.alex_homework_due_tomorrow").state == "2"
    nxt = state(hass, "sensor.alex_next_homework_due")
    assert nxt.state == "2026-10-16"
    assert nxt.attributes["description"] == "Read chapter 3\nAnswer questions 1-4"
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
    assert today.attributes["state_class"] == "total"
    assert today.attributes["last_reset"] == "2026-10-16T00:00:00+01:00"
    week = state(hass, "sensor.alex_spent_this_week")
    assert week.state == "6.2"
    assert week.attributes["state_class"] == "total"
    assert week.attributes["last_reset"] == "2026-10-12T00:00:00+01:00"
    assert "last_reset" not in balance.attributes
    assert state(hass, "sensor.alex_last_purchase").state == "2026-10-16T11:34:38+00:00"
    top_up = state(hass, "sensor.alex_last_top_up")
    assert top_up.state == "15.0"
    assert top_up.attributes["unit_of_measurement"] == "GBP"
    assert top_up.attributes["time"] == "2026-10-15T08:00:00+01:00"


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
    fake.errors["EduLink.Login"] = "Incorrect username or password"
    await setup.runtime_data.catering.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_login_failure(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Bad credentials at startup."""
    fake.errors["EduLink.Login"] = "Incorrect username or password"
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


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
    assert "EduLink.Catering" not in fake.methods()
    assert hass.states.get("sensor.alex_catering_balance") is None
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
    for secret in ("secret", "p_example", "Alex", "English task", "PIZZA", "7001"):
        assert secret not in text
    assert diag["learners"][0]["homework"]["current"] == 5
