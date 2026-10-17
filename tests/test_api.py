"""Tests for the Edulink client: URL handling and session recovery."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.edulink.api import (
    EdulinkAuthError,
    EdulinkClient,
    EdulinkError,
    EdulinkPermissionError,
    _server_url,
)

from .conftest import SERVER, FakeEdulink


@pytest.mark.parametrize(
    "server",
    [
        "https://www9.edulinkone.com/api/",
        "https://www9.edulinkone.com/api",
        "https://www9.edulinkone.com/",
        "https://www9.edulinkone.com",
    ],
)
def test_server_url(server: str) -> None:
    """FromCode has returned the server with and without /api/."""
    assert _server_url(server) == SERVER


def client(hass: HomeAssistant) -> EdulinkClient:
    return EdulinkClient(async_get_clientsession(hass), SERVER, "999", "u", "p")


async def test_concurrent_expiry_logs_in_once(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """Two calls hitting an expired session share one re-login."""
    c = client(hass)
    await c.login()
    fake.expire()
    fake.calls.clear()
    results = await asyncio.gather(c.catering("7001"), c.homework("7001"))
    assert all(r["success"] for r in results)
    assert fake.methods().count("EduLink.Login") == 1


async def test_rejected_after_relogin_is_not_an_auth_error(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """Credentials that just worked must not trigger reauth."""
    c = client(hass)
    await c.login()
    fake.reject_sessions = True
    with pytest.raises(EdulinkError) as err:
        await c.catering("7001")
    assert not isinstance(err.value, EdulinkAuthError)


async def test_failed_relogin_is_an_auth_error(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """A rejected login on expiry means the credentials changed."""
    c = client(hass)
    await c.login()
    fake.expire()
    fake.errors["EduLink.Login"] = "Incorrect username or password"
    with pytest.raises(EdulinkAuthError):
        await c.catering("7001")


async def test_permission_error(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """'Not authorised' maps to a permission error, not reauth."""
    c = client(hass)
    fake.learner_errors[("EduLink.Catering", "7001")] = (
        "You are not authorised to carry out this action"
    )
    with pytest.raises(EdulinkPermissionError):
        await c.catering("7001")
