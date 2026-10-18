"""Async client for the Edulink One JSON-RPC API.

Edulink has no published documentation. Behaviour here was established by
probing the live service.

This module deliberately has no Home Assistant imports so it can be extracted
into a standalone library later.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging
from typing import Any
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

PROVISIONING_URL = "https://provisioning.edulinkone.com/"
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Errors are returned with HTTP 200 and result.success = false; these are the
# result.error strings we act on.
ERR_NOT_LOGGED_IN = "You are not logged in"
ERR_NO_AUTHTOKEN = "Authtoken not present"
ERR_NOT_AUTHORISED = "You are not authorised"
# Wrong username and wrong password both give "Login failed: The username or
# password is incorrect. ..." (checked 2026-10-18). Other login errors, such as
# throttling, are treated as temporary rather than as bad credentials.
ERR_LOGIN_FAILED = "Login failed"


class EdulinkError(Exception):
    """Base error."""


class EdulinkConnectionError(EdulinkError):
    """Network failure, timeout or unparseable response."""


class EdulinkRateLimitError(EdulinkConnectionError):
    """Edulink asked us to slow down (HTTP 429)."""

    def __init__(self, message: str, retry_after: timedelta | None) -> None:
        """Initialise with the wait Edulink asked for, if it gave one."""
        super().__init__(message)
        self.retry_after = retry_after


class EdulinkAuthError(EdulinkError):
    """Login rejected."""


class EdulinkPermissionError(EdulinkError):
    """The account is not permitted to call this method."""


class EdulinkSchoolNotFound(EdulinkError):
    """School code or postcode not recognised."""


class _SessionExpired(EdulinkError):
    """Token missing or expired; retried internally after re-login."""


@dataclass(slots=True)
class SchoolLookup:
    """Result of School.FromCode."""

    server: str
    establishment_id: str


@dataclass(slots=True)
class SchoolDetails:
    """Result of EduLink.SchoolDetails."""

    establishment_id: str
    name: str
    idp_only: bool


@dataclass(slots=True)
class Learner:
    """A learner the account can see."""

    id: str
    forename: str
    surname: str


@dataclass(slots=True)
class LoginResult:
    """The parts of EduLink.Login we use."""

    user_id: str
    user_types: list[str]
    learners: list[Learner]
    modules: set[str] = field(default_factory=set)


def _server_url(server: str) -> str:
    """Normalise a server URL so that "?method=X" can be appended.

    FromCode currently returns ".../api/"; older responses omitted "api/".
    """
    server = server.rstrip("/")
    if not server.endswith("/api"):
        server += "/api"
    return server + "/"


async def _post(
    session: aiohttp.ClientSession,
    url: str,
    method: str,
    params: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any]:
    """Send one JSON-RPC request and return its result object."""
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "uuid": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }
    headers = {"X-API-Method": method}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with session.post(
            f"{url}?method={method}", json=payload, headers=headers, timeout=TIMEOUT
        ) as resp:
            if resp.status == 429:
                raise EdulinkRateLimitError(
                    f"{method}: rate limited",
                    _retry_after(resp.headers.get("Retry-After")),
                )
            resp.raise_for_status()
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise EdulinkConnectionError(f"{method}: {err}") from err

    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        raise EdulinkConnectionError(f"{method}: malformed response")
    if result.get("success"):
        return result

    error = str(result.get("error") or "unknown error")
    if error.startswith((ERR_NOT_LOGGED_IN, ERR_NO_AUTHTOKEN)):
        raise _SessionExpired(error)
    if error.startswith(ERR_NOT_AUTHORISED):
        raise EdulinkPermissionError(f"{method}: {error}")
    raise EdulinkError(f"{method}: {error}")


def _retry_after(value: str | None) -> timedelta | None:
    """Parse a Retry-After header: delay in seconds, or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return timedelta(seconds=int(value))
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(when - datetime.now(UTC), timedelta(0))


async def find_school(session: aiohttp.ClientSession, code: str) -> SchoolLookup:
    """Resolve a school code or postcode to its server and establishment id."""
    try:
        result = await _post(
            session, PROVISIONING_URL, "School.FromCode", {"code": code.strip()}
        )
    except (EdulinkPermissionError, _SessionExpired) as err:
        raise EdulinkError(str(err)) from err
    except EdulinkConnectionError:
        raise
    except EdulinkError as err:
        raise EdulinkSchoolNotFound(str(err)) from err
    school = result.get("school") or {}
    if not school.get("server") or school.get("school_id") is None:
        raise EdulinkSchoolNotFound(code)
    return SchoolLookup(
        server=_server_url(school["server"]),
        establishment_id=str(school["school_id"]),
    )


class EdulinkClient:
    """Authenticated client for one Edulink account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        server: str,
        establishment_id: str,
        username: str,
        password: str,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._url = _server_url(server)
        self._establishment_id = str(establishment_id)
        self._username = username
        self._password = password
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def school_details(self) -> SchoolDetails:
        """Return the school name and whether it only allows SSO login."""
        result = await _post(
            self._session,
            self._url,
            "EduLink.SchoolDetails",
            {"establishment_id": self._establishment_id, "from_app": False},
        )
        est = result.get("establishment") or {}
        return SchoolDetails(
            establishment_id=str(est.get("id", self._establishment_id)),
            name=str(est.get("name", "")),
            idp_only=bool(est.get("idp_only", False)),
        )

    async def login(self) -> LoginResult:
        """Log in, store the token and return account details."""
        try:
            result = await _post(
                self._session,
                self._url,
                "EduLink.Login",
                {
                    "establishment_id": self._establishment_id,
                    "from_app": False,
                    "username": self._username,
                    "password": self._password,
                },
            )
        except EdulinkError as err:
            if str(err).startswith(f"EduLink.Login: {ERR_LOGIN_FAILED}"):
                raise EdulinkAuthError(str(err)) from err
            raise

        token = result.get("authtoken")
        if not token:
            raise EdulinkAuthError("login returned no authtoken")
        self._token = token

        user = result.get("user") or {}
        types = [str(t) for t in user.get("types") or []]
        children = result.get("children") or []
        # A learner logging in as themselves has no children list.
        learners = [_learner(c) for c in children] if children else [_learner(user)]
        modules = {str(m.get("id")) for m in result.get("learner_menu") or []}
        return LoginResult(
            user_id=str(user.get("id", "")),
            user_types=types,
            learners=learners,
            modules=modules,
        )

    async def logout(self) -> None:
        """End the session. Errors are ignored."""
        if not self._token:
            return
        try:
            await _post(self._session, self._url, "EduLink.Logout", {}, self._token)
        except EdulinkError as err:
            _LOGGER.debug("Logout failed: %s", err)
        self._token = None

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call an authenticated method, logging in again once if needed."""
        if self._token is None:
            await self._relogin(None)
        token = self._token
        try:
            return await _post(self._session, self._url, method, params, token)
        except _SessionExpired:
            _LOGGER.debug("Session expired calling %s, logging in again", method)
            await self._relogin(token)
        try:
            return await _post(self._session, self._url, method, params, self._token)
        except _SessionExpired as err:
            # The credentials have just worked, so this is not a reauth case.
            raise EdulinkError(
                f"{method}: rejected straight after login: {err}"
            ) from err

    async def _relogin(self, stale_token: str | None) -> None:
        """Log in unless another caller already replaced the stale token."""
        async with self._login_lock:
            if self._token is not None and self._token != stale_token:
                return
            await self.login()

    async def timetable(self, learner_id: str, day: date) -> dict[str, Any]:
        """Timetable from the Monday of day's week, about five weeks."""
        return await self.call(
            "EduLink.Timetable", {"date": day.isoformat(), "learner_id": learner_id}
        )

    async def homework(self, learner_id: str) -> dict[str, Any]:
        """Current and past homework."""
        return await self.call(
            "EduLink.Homework", {"format": 2, "learner_id": learner_id}
        )

    async def homework_details(
        self, learner_id: str, homework_id: str, source: str
    ) -> dict[str, Any]:
        """Full homework record including the HTML description."""
        return await self.call(
            "EduLink.HomeworkDetails",
            {"homework_id": homework_id, "source": source, "learner_id": learner_id},
        )

    async def catering(self, learner_id: str) -> dict[str, Any]:
        """Catering balance and recent transactions."""
        return await self.call("EduLink.Catering", {"learner_id": learner_id})

    async def achievement(self, learner_id: str) -> dict[str, Any]:
        """Achievements, with points."""
        return await self.call("EduLink.Achievement", {"learner_id": learner_id})

    async def behaviour(self, learner_id: str) -> dict[str, Any]:
        """Behaviour events."""
        return await self.call(
            "EduLink.Behaviour", {"format": 2, "learner_id": learner_id}
        )

    async def attendance(self, learner_id: str) -> dict[str, Any]:
        """Statutory attendance for the current and previous month."""
        return await self.call(
            "EduLink.Attendance", {"format": 3, "learner_id": learner_id}
        )

    async def clubs(self, learner_id: str) -> dict[str, Any]:
        """Clubs the learner belongs to."""
        return await self.call(
            "EduLink.Clubs", {"member": True, "learner_id": learner_id}
        )

    async def status(self) -> dict[str, Any]:
        """Account-wide unread counts. Takes no learner."""
        return await self.call("EduLink.Status", {"last_visible": 0})


def _learner(raw: dict[str, Any]) -> Learner:
    return Learner(
        id=str(raw.get("id", "")),
        forename=str(raw.get("forename") or ""),
        surname=str(raw.get("surname") or ""),
    )
