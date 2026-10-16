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
    MODULE_CATERING,
    MODULE_HOMEWORK,
    MODULE_TIMETABLE,
)
from .coordinator import (
    CateringCoordinator,
    EdulinkConfigEntry,
    EdulinkRuntimeData,
    HomeworkCoordinator,
    TimetableCoordinator,
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
    # An empty menu would be unusual; assume everything rather than nothing.
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
        runtime.catering = CateringCoordinator(hass, entry, client, learners)

    for coordinator in (runtime.timetable, runtime.homework, runtime.catering):
        if coordinator is not None:
            await coordinator.async_config_entry_first_refresh()
    if runtime.timetable is not None:
        runtime.timetable.async_start_clock()

    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EdulinkConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.logout()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: EdulinkConfigEntry) -> None:
    """Reload after options change."""
    await hass.config_entries.async_reload(entry.entry_id)
