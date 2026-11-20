"""Tests for the Edulink client: URL handling and session recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.edulink.api import (
    EdulinkAuthError,
    EdulinkClient,
    EdulinkConnectionError,
    EdulinkError,
    EdulinkPermissionError,
    EdulinkRateLimitError,
    _retry_after,
    _server_url,
)

from .conftest import LOGIN_FAILED, SERVER, FakeEdulink


@pytest.mark.parametrize(
    "server",
    [
        "https://server.example.invalid/api/",
        "https://server.example.invalid/api",
        "https://server.example.invalid/",
        "https://server.example.invalid",
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
    fake.errors["EduLink.Login"] = LOGIN_FAILED
    with pytest.raises(EdulinkAuthError):
        await c.catering("7001")


async def test_other_login_error_is_not_an_auth_error(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """A login error other than "Login failed" (e.g. throttling) is temporary."""
    fake.errors["EduLink.Login"] = "Too many requests, please try again later"
    with pytest.raises(EdulinkError) as err:
        await client(hass).login()
    assert not isinstance(err.value, EdulinkAuthError)


@pytest.mark.parametrize("clubs", ["oops", None, {"a": 1}])
async def test_clubs_that_are_not_a_list_are_a_bad_response(
    hass: HomeAssistant, fake: FakeEdulink, clubs: object
) -> None:
    """A present but non-list "clubs" is an error, so no one "leaves" their clubs."""
    fake.responses["EduLink.Clubs"] = {"result": {"success": True, "clubs": clubs}}
    with pytest.raises(EdulinkConnectionError):
        await client(hass).clubs("7001")


async def test_clubs_missing_or_empty_is_no_clubs(
    hass: HomeAssistant, fake: FakeEdulink
) -> None:
    """An empty list, or no key at all, is accepted as a learner with no clubs."""
    for result in ({"success": True, "clubs": []}, {"success": True}):
        fake.responses["EduLink.Clubs"] = {"result": result}
        assert await client(hass).clubs("7001") == result


async def test_permission_error(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """'Not authorised' maps to a permission error, not reauth."""
    c = client(hass)
    fake.learner_errors[("EduLink.Catering", "7001")] = (
        "You are not authorised to carry out this action"
    )
    with pytest.raises(EdulinkPermissionError):
        await c.catering("7001")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", timedelta(seconds=120)),
        (None, None),
        ("", None),
        ("soon", None),
        ("-5", None),
        # Digits that are not ASCII are not a number of seconds.
        ("²", None),
        # A huge value is clamped to a day rather than overflowing.
        ("86401", timedelta(days=1)),
        ("9" * 30, timedelta(days=1)),
        ("9" * 5000, timedelta(days=1)),
        ("0" * 5000 + "5", timedelta(seconds=5)),
        ("0", timedelta(0)),
        # A date in the past means no wait.
        ("Wed, 21 Oct 2015 07:28:00 GMT", timedelta(0)),
    ],
)
def test_retry_after(header: str | None, expected: timedelta | None) -> None:
    """Retry-After is seconds or an HTTP date; anything else is ignored."""
    assert _retry_after(header) == expected


def test_retry_after_http_date() -> None:
    """An HTTP date in the future gives the time until then."""
    when = datetime.now(UTC) + timedelta(hours=1)
    delay = _retry_after(when.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert timedelta(minutes=59) < delay <= timedelta(hours=1)


async def test_rate_limited(hass: HomeAssistant, fake: FakeEdulink) -> None:
    """HTTP 429 raises a rate limit error carrying Retry-After."""
    api = client(hass)
    await api.login()
    fake.rate_limits["EduLink.Catering"] = "300"
    with pytest.raises(EdulinkRateLimitError) as err:
        await api.catering("7001")
    assert err.value.retry_after == timedelta(seconds=300)
