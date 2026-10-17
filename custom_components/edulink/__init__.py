"""The Edulink One integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EdulinkAuthError, EdulinkClient, EdulinkError
from .const import (
    CONF_ESTABLISHMENT_ID,
    CONF_LEARNERS,
    CONF_SCHOOL_NAME,
    CONF_SERVER,
    MODULE_ACHIEVEMENT,
    MODULE_ATTENDANCE,
    MODULE_BEHAVIOUR,
    MODULE_CATERING,
    MODULE_CLUBS,
    MODULE_HOMEWORK,
    MODULE_TIMETABLE,
)
from .coordinator import (
    CateringCoordinator,
    EdulinkConfigEntry,
    EdulinkRuntimeData,
    HomeworkCoordinator,
    RecordCoordinator,
    StatusCoordinator,
    TimetableCoordinator,
    async_start_clock,
)
from .models import (
    parse_achievement,
    parse_attendance,
    parse_behaviour,
    parse_clubs,
)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
    Platform.TODO,
]


async def async_setup_entry(hass: HomeAssistant, entry: EdulinkConfigEntry) -> bool:
    """Set up Edulink One from a config entry."""
    client = EdulinkClient(
        async_get_clientsession(hass),
        entry.data[CONF_SERVER],
        entry.data[CONF_ESTABLISHMENT_ID],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        login = await client.login()
    except EdulinkAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EdulinkError as err:
        raise ConfigEntryNotReady(str(err)) from err

    selected = set(entry.data[CONF_LEARNERS])
    learners = [learner for learner in login.learners if learner.id in selected]
    # An empty menu would be unusual; assume the core modules rather than nothing.
    # The school record modules are only ever created when the school lists them.
    modules = login.modules or {MODULE_TIMETABLE, MODULE_HOMEWORK, MODULE_CATERING}

    runtime = EdulinkRuntimeData(
        client=client,
        school_name=entry.data[CONF_SCHOOL_NAME],
        establishment_id=entry.data[CONF_ESTABLISHMENT_ID],
        learners=learners,
    )
    if MODULE_TIMETABLE in modules:
        runtime.timetable = TimetableCoordinator(hass, entry, client, learners)
    if MODULE_HOMEWORK in modules:
        runtime.homework = HomeworkCoordinator(hass, entry, client, learners)
    if MODULE_CATERING in modules:
        runtime.catering = CateringCoordinator(
            hass, entry, client, learners, runtime.timetable
        )
    if MODULE_ACHIEVEMENT in modules:
        runtime.achievement = RecordCoordinator(
            hass,
            entry,
            client,
            learners,
            "achievement",
            client.achievement,
            parse_achievement,
        )
    if MODULE_BEHAVIOUR in modules:
        runtime.behaviour = RecordCoordinator(
            hass,
            entry,
            client,
            learners,
            "behaviour",
            client.behaviour,
            parse_behaviour,
        )
    if MODULE_ATTENDANCE in modules:
        runtime.attendance = RecordCoordinator(
            hass,
            entry,
            client,
            learners,
            "attendance",
            client.attendance,
            parse_attendance,
        )
    if MODULE_CLUBS in modules:
        runtime.clubs = RecordCoordinator(
            hass, entry, client, learners, "clubs", client.clubs, parse_clubs
        )
    # Status is account-wide and not in learner_menu, so try it instead.
    runtime.status = StatusCoordinator(hass, entry, client)

    core = (runtime.timetable, runtime.homework, runtime.catering)
    try:
        for coordinator in core:
            if coordinator is not None:
                await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Setup will be retried with a fresh login; close this session.
        await client.logout()
        raise

    # The optional modules must not stop the core ones loading. If one fails
    # its entities start unavailable and it retries on its own schedule.
    for coordinator in (
        runtime.achievement,
        runtime.behaviour,
        runtime.attendance,
        runtime.clubs,
        runtime.status,
    ):
        if coordinator is not None:
            await coordinator.async_refresh()
    if runtime.status.last_update_success and runtime.status.data is None:
        # Not authorised for this account.
        runtime.status = None
    entry.runtime_data = runtime
    async_start_clock(hass, entry, runtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EdulinkConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.logout()
    return unloaded
