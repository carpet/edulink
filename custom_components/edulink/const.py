"""Constants for the Edulink One integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "edulink"

CONF_SCHOOL_CODE: Final = "school_code"
CONF_SERVER: Final = "server"
CONF_ESTABLISHMENT_ID: Final = "establishment_id"
CONF_SCHOOL_NAME: Final = "school_name"
CONF_USER_ID: Final = "user_id"
CONF_LEARNERS: Final = "learners"

CONF_LOW_BALANCE: Final = "low_balance_threshold"
CONF_HOMEWORK_INTERVAL: Final = "homework_interval"
CONF_CATERING_INTERVAL: Final = "catering_interval"
CONF_HOMEWORK_DAYS_AHEAD: Final = "homework_days_ahead"
CONF_KIT_RULES: Final = "kit_rules"
CONF_ACADEMIC_YEAR_START: Final = "academic_year_start_month"
CONF_CALENDAR_SUBMITTED: Final = "calendar_show_submitted"
CONF_SCHOOL_TODAY_ENDS: Final = "school_today_ends_after_last_lesson"

DEFAULT_LOW_BALANCE: Final = 5.0
DEFAULT_HOMEWORK_INTERVAL: Final = 30  # minutes
DEFAULT_CATERING_INTERVAL: Final = 15  # minutes
DEFAULT_HOMEWORK_DAYS_AHEAD: Final = 7
# Edulink has no term or year dates; schools in England start in September.
DEFAULT_ACADEMIC_YEAR_START: Final = 9
DEFAULT_CALENDAR_SUBMITTED: Final = False
DEFAULT_SCHOOL_TODAY_ENDS: Final = False
# "subject keyword = item" per line.
DEFAULT_KIT_RULES: Final = (
    "Physical Education = PE kit\n"
    "PE = PE kit\n"
    "Food = Food Tech ingredients\n"
    "Art = Art materials"
)

TIMETABLE_INTERVAL: Final = timedelta(hours=6)
# Achievement, behaviour, attendance and clubs change slowly.
RECORD_INTERVAL: Final = timedelta(hours=12)
STATUS_INTERVAL: Final = timedelta(minutes=30)
OFF_HOURS_INTERVAL: Final = timedelta(hours=2)
# Retry delay after a failed poll, so a blip does not cost a full interval.
RETRY_INTERVAL: Final = timedelta(minutes=15)
# Repeated failures double the retry delay up to this, or the normal interval
# if that is longer. A Retry-After from Edulink is honoured up to a day.
MAX_BACKOFF: Final = timedelta(hours=2)
MAX_RETRY_AFTER: Final = timedelta(days=1)

# Overdue homework only looks back this far; older "Not submitted" items are noise.
OVERDUE_WINDOW_DAYS: Final = 14
# Past homework kept for the calendar.
PAST_HOMEWORK_DAYS: Final = 30

# Module ids from the login response's learner_menu.
MODULE_TIMETABLE: Final = "timetable"
MODULE_HOMEWORK: Final = "homework"
MODULE_CATERING: Final = "catering"
MODULE_ACHIEVEMENT: Final = "achievement"
MODULE_BEHAVIOUR: Final = "behaviour"
MODULE_ATTENDANCE: Final = "attendance"
MODULE_CLUBS: Final = "clubs"

CURRENCY: Final = "GBP"
