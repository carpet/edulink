"""Fixtures for Edulink One tests.

All fixture data is synthetic; no real school, learner or staff data.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

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
NOW = "2026-10-16 10:00:00+01:00"  # Friday, period 2 (English)


def load(name: str) -> dict[str, Any]:
    """Load a fixture."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


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
        }
        self.errors: dict[str, str] = {}
        self.network_errors: set[str] = set()
        self.expire_session = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        mock.post(SERVER, side_effect=self._handle)
        mock.post(PROVISIONING, side_effect=self._handle)

    def methods(self) -> list[str]:
        """Names of the methods called so far."""
        return [method for method, _ in self.calls]

    def set_result(self, method: str, **changes: Any) -> None:
        """Change fields of a method's result."""
        result = copy.deepcopy(self.responses[method])
        result["result"].update(changes)
        self.responses[method] = result

    async def _handle(self, method: str, url: Any, data: dict[str, Any]):
        rpc = data["method"]
        self.calls.append((rpc, data["params"]))
        if rpc in self.network_errors:
            return AiohttpClientMockResponse(method, url, exc=aiohttp.ClientError())
        if self.expire_session and rpc != "EduLink.Login":
            self.expire_session = False
            return self._error(method, url, rpc, "You are not logged in")
        if rpc in self.errors:
            return self._error(method, url, rpc, self.errors[rpc])
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
def entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """A config entry, not yet added to hass."""
    return MockConfigEntry(
        domain=DOMAIN, unique_id="999_5001", data=entry_data, title="Example"
    )
