"""Tests for the Edulink One config flow."""

from __future__ import annotations

import copy
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.edulink.const import (
    CONF_CATERING_INTERVAL,
    CONF_HOMEWORK_INTERVAL,
    CONF_LEARNERS,
    CONF_LOW_BALANCE,
    DOMAIN,
)

from .conftest import SERVER, FakeEdulink

CREDS = {"username": "p_example", "password": "secret"}


@pytest.fixture
def mock_setup_entry():
    """Stop flows that create or reload an entry from setting it up."""
    with patch("custom_components.edulink.async_setup_entry", return_value=True):
        yield


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"school_code": "AB1 2CD"}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_full_flow_two_children(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """Postcode, credentials, child selection."""
    result = await _start(hass)
    assert result["step_id"] == "credentials"
    assert result["description_placeholders"] == {"school": "Example Academy"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "learners"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LEARNERS: ["7002"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example Academy (Sam)"
    assert result["result"].unique_id == "999_5001"
    assert result["data"] == {
        "server": SERVER,
        "establishment_id": "999",
        "school_name": "Example Academy",
        **CREDS,
        "user_id": "5001",
        CONF_LEARNERS: ["7002"],
    }
    # Validation login is closed again.
    assert fake.methods()[-1] == "EduLink.Logout"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_single_learner_skips_selection(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """A learner login (no children) creates the entry directly."""
    login = copy.deepcopy(fake.responses["EduLink.Login"])
    login["result"]["children"] = []
    login["result"]["user"]["types"] = ["learner"]
    fake.responses["EduLink.Login"] = login

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LEARNERS] == ["5001"]


async def test_school_not_found(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """Unknown postcode."""
    fake.errors["School.FromCode"] = "School not found"
    result = await _start(hass)
    assert result["errors"] == {"base": "school_not_found"}


async def test_school_lookup_network_error(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """Network failure during lookup."""
    fake.network_errors.add("School.FromCode")
    result = await _start(hass)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_sso_only_school(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """Microsoft-only schools are rejected."""
    details = copy.deepcopy(fake.responses["EduLink.SchoolDetails"])
    details["result"]["establishment"]["idp_only"] = True
    fake.responses["EduLink.SchoolDetails"] = details
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sso_only"


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (
            lambda f: f.errors.__setitem__("EduLink.Login", "Login failed"),
            "invalid_auth",
        ),
        (lambda f: f.network_errors.add("EduLink.Login"), "cannot_connect"),
    ],
)
async def test_login_errors(
    hass: HomeAssistant, fake: FakeEdulink, setup, error
) -> None:
    """Login failures are shown and recoverable."""
    result = await _start(hass)
    setup(fake)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": error}

    fake.errors.clear()
    fake.network_errors.clear()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "learners"


async def test_already_configured(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Reauth updates the password."""
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "p_example", "password": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_reauth_wrong_account(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Reauth with another account's credentials is refused."""
    entry.add_to_hass(hass)
    fake.responses["EduLink.Login"]["result"]["user"]["id"] = "6000"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["reason"] == "wrong_account"


async def test_options(
    hass: HomeAssistant, fake: FakeEdulink, entry: MockConfigEntry
) -> None:
    """Options are stored."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_LOW_BALANCE: 20, CONF_HOMEWORK_INTERVAL: 60, CONF_CATERING_INTERVAL: 30},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LOW_BALANCE] == 20
    # Reloaded with the new threshold: £11 is now low.
    assert hass.states.get("binary_sensor.alex_low_catering_balance").state == "on"
