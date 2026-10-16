"""Constants for the Edulink One integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "edulink"

PROVISIONING_URL: Final = "https://provisioning.edulinkone.com/"

CONF_SCHOOL_CODE: Final = "school_code"
CONF_SERVER: Final = "server"
CONF_ESTABLISHMENT_ID: Final = "establishment_id"
CONF_SCHOOL_NAME: Final = "school_name"
CONF_USER_ID: Final = "user_id"
CONF_LEARNERS: Final = "learners"

CONF_LOW_BALANCE: Final = "low_balance_threshold"
CONF_HOMEWORK_INTERVAL: Final = "homework_interval"
CONF_CATERING_INTERVAL: Final = "catering_interval"

DEFAULT_LOW_BALANCE: Final = 5.0
DEFAULT_HOMEWORK_INTERVAL: Final = 30  # minutes
DEFAULT_CATERING_INTERVAL: Final = 15  # minutes

TIMETABLE_INTERVAL: Final = timedelta(hours=6)
OFF_HOURS_INTERVAL: Final = timedelta(hours=2)

# Overdue homework only looks back this far; older "Not submitted" items are noise.
OVERDUE_WINDOW_DAYS: Final = 14
# Past homework kept for the calendar.
PAST_HOMEWORK_DAYS: Final = 30

# Module ids from the login response's learner_menu.
MODULE_TIMETABLE: Final = "timetable"
MODULE_HOMEWORK: Final = "homework"
MODULE_CATERING: Final = "catering"

CURRENCY: Final = "GBP"
