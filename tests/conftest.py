"""Fixtures for Edulink One tests.

All fixture data is synthetic; no real school, learner or staff data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.edulink.const import (
    CONF_ESTABLISHMENT_ID,
    CONF_LEARNERS,
    CONF_SCHOOL_NAME,
    CONF_SERVER,
    CONF_USER_ID,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = "https://www9.edulinkone.com/api/"
PROVISIONING = "https://provisioning.edulinkone.com/"
NOW = "2026-10-16 10:00:00+01:00"
# Edulink's response to a wrong username or password.
LOGIN_FAILED = (
    "Login failed: The username or password is incorrect. "
    "Please try typing your password again"
)  # Friday, period 2 (English)


def load(name: str) -> dict[str, Any]:
    """Load a fixture."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
async def uk_time_zone(hass):
    """Run Home Assistant in the school's time zone, as real users do."""
    await hass.config.async_set_time_zone("Europe/London")


@pytest.fixture
def entity_registry_enabled_by_default():
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


class FakeEdulink:
    """Routes JSON-RPC calls to fixtures, with switchable failures."""

    def __init__(self, mock: AiohttpClientMocker) -> None:
        self.responses: dict[str, dict[str, Any]] = {
            "School.FromCode": load("school_from_code.json"),
            "EduLink.SchoolDetails": load("school_details.json"),
            "EduLink.Login": load("login.json"),
            "EduLink.Logout": {"result": {"success": True}},
            "EduLink.Timetable": load("timetable.json"),
            "EduLink.Homework": load("homework.json"),
            "EduLink.HomeworkDetails": load("homework_details.json"),
            "EduLink.Catering": load("catering.json"),
            "EduLink.Achievement": load("achievement.json"),
            "EduLink.Behaviour": load("behaviour.json"),
            "EduLink.Attendance": load("attendance.json"),
            "EduLink.Clubs": load("clubs.json"),
            "EduLink.Status": load("status.json"),
        }
        # Per-learner overrides, keyed by (method, learner_id).
        self.learner_responses: dict[tuple[str, str], dict[str, Any]] = {}
        self.learner_errors: dict[tuple[str, str], str] = {}
        self.errors: dict[str, str] = {}
        self.network_errors: set[str] = set()
        # HTTP 429 responses, keyed by method or (method, learner_id), with
        # the Retry-After header value or None for no header.
        self.rate_limits: dict[str | tuple[str, str], str | None] = {}
        # Session state: Login opens a session, Logout or expire() ends it.
        self.logged_in = False
        # Simulate a server that rejects every session, even straight after login.
        self.reject_sessions = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        mock.post(SERVER, side_effect=self._handle)
        mock.post(PROVISIONING, side_effect=self._handle)

    def methods(self) -> list[str]:
        """Names of the methods called so far."""
        return [method for method, _ in self.calls]

    def expire(self) -> None:
        """End the current session, as Edulink does after ~2h idle."""
        self.logged_in = False

    async def _handle(self, method: str, url: Any, data: dict[str, Any]):
        rpc = data["method"]
        params = data["params"]
        self.calls.append((rpc, params))
        key = (rpc, str(params.get("learner_id")))
        if rpc in self.network_errors:
            return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError())
        for limited in (key, rpc):
            if limited in self.rate_limits:
                retry_after = self.rate_limits[limited]
                return AiohttpClientMockResponse(
                    method,
                    url,
                    status=429,
                    headers={"Retry-After": retry_after} if retry_after else None,
                )
        if rpc in self.errors:
            return self._error(method, url, rpc, self.errors[rpc])
        if rpc == "EduLink.Login":
            self.logged_in = True
        elif rpc == "EduLink.Logout":
            self.logged_in = False
        elif (
            rpc.startswith("EduLink.")
            and rpc != "EduLink.SchoolDetails"
            and (not self.logged_in or self.reject_sessions)
        ):
            return self._error(method, url, rpc, "You are not logged in")
        if key in self.learner_errors:
            return self._error(method, url, rpc, self.learner_errors[key])
        if key in self.learner_responses:
            return AiohttpClientMockResponse(
                method, url, json=self.learner_responses[key]
            )
        if rpc not in self.responses:
            return self._error(method, url, rpc, f"Invalid API method {rpc}")
        return AiohttpClientMockResponse(method, url, json=self.responses[rpc])

    @staticmethod
    def _error(method: str, url: Any, rpc: str, error: str):
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "jsonrpc": "2.0",
                "result": {"success": False, "error": error, "method": rpc},
                "id": "1",
            },
        )


@pytest.fixture
def fake(aioclient_mock: AiohttpClientMocker) -> FakeEdulink:
    """A fake Edulink server."""
    return FakeEdulink(aioclient_mock)


@pytest.fixture
def entry_data() -> dict[str, Any]:
    """Config entry data for one of two children."""
    return {
        CONF_SERVER: SERVER,
        CONF_ESTABLISHMENT_ID: "999",
        CONF_SCHOOL_NAME: "Example Academy",
        "username": "p_example",
        "password": "secret",
        CONF_USER_ID: "5001",
        CONF_LEARNERS: ["7001"],
    }


@pytest.fixture
def two_learner_entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """A config entry for both children."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="999_5001",
        data={**entry_data, CONF_LEARNERS: ["7001", "7002"]},
        title="Example",
    )


@pytest.fixture
def entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """A config entry, not yet added to hass."""
    return MockConfigEntry(
        domain=DOMAIN, unique_id="999_5001", data=entry_data, title="Example"
    )
