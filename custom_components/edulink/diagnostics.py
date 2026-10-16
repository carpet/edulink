"""Diagnostics for Edulink One.

Deliberately reports structure and counts only, never names, homework text,
teachers, rooms or purchases: this is a child's school data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_LEARNERS, CONF_USER_ID
from .coordinator import EdulinkConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_USER_ID, CONF_LEARNERS, "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EdulinkConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    learners: list[dict[str, Any]] = []
    for index, learner in enumerate(runtime.learners):
        info: dict[str, Any] = {"learner": index}
        if runtime.timetable and (tt := (runtime.timetable.data or {}).get(learner.id)):
            info["timetable"] = {
                "days": len(tt.days),
                "first": min(tt.days).isoformat() if tt.days else None,
                "last": max(tt.days).isoformat() if tt.days else None,
                "lessons": sum(len(d.lessons) for d in tt.days.values()),
                "cycle_weeks": sorted({d.cycle_week or "?" for d in tt.days.values()}),
            }
        if runtime.homework and (hw := (runtime.homework.data or {}).get(learner.id)):
            info["homework"] = {
                "current": len(hw.current),
                "past_recent": len(hw.past),
                "with_description": sum(1 for h in hw.current if h.description),
                "statuses": sorted({h.status for h in (*hw.current, *hw.past)}),
            }
        if runtime.catering and (c := (runtime.catering.data or {}).get(learner.id)):
            info["catering"] = {
                "has_balance": c.balance is not None,
                "transactions": len(c.transactions),
                "top_ups": len(c.top_ups),
            }
        learners.append(info)

    coordinators = {
        name: {
            "last_update_success": coord.last_update_success,
            "update_interval": str(coord.update_interval),
        }
        for name, coord in (
            ("timetable", runtime.timetable),
            ("homework", runtime.homework),
            ("catering", runtime.catering),
        )
        if coord is not None
    }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "coordinators": coordinators,
        "learners": learners,
    }
