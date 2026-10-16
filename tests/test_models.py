"""Tests for the pure parsing and derivation functions."""

from __future__ import annotations

from datetime import date, datetime

from custom_components.edulink.models import (
    SCHOOL_TZ,
    html_to_text,
    parse_catering,
    parse_homework,
    parse_timetable,
)

from .conftest import load


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    """A school-local time in autumn 2026."""
    return datetime(2026, month, day, hour, minute, tzinfo=SCHOOL_TZ)


def timetable():
    return parse_timetable(load("timetable.json")["result"])


def test_timetable_days_and_holiday_gap() -> None:
    """Holiday weeks are absent, not empty."""
    tt = timetable()
    assert len(tt.days) == 15
    assert tt.day(date(2026, 10, 26)) is None
    assert tt.next_school_day(date(2026, 10, 23)).date == date(2026, 11, 2)


def test_lessons_sorted_and_registration_flagged() -> None:
    """Lessons arrive unordered and must be sorted by period start."""
    day = timetable().day(date(2026, 10, 16))
    assert [lesson.period for lesson in day.lessons] == ["Reg", "1", "2", "3", "4", "5"]
    assert day.lessons[0].is_registration
    assert [lesson.subject for lesson in day.teaching] == [
        "Mathematics",
        "English",
        "Drama",
        "History",
        "Physical Education",
    ]
    assert day.lessons[4].room is None
    assert day.start == at(16, 8, 30)
    assert day.end == at(16, 15)
    assert day.cycle_week == "A"


def test_current_state() -> None:
    """Current lesson across a school day."""
    tt = timetable()
    assert tt.current_state(at(16, 7))[0] == "Not in school"
    assert tt.current_state(at(16, 8, 40))[0] == "Registration"
    state, lesson = tt.current_state(at(16, 10))
    assert state == "English"
    assert lesson.room == "R2"
    assert tt.current_state(at(16, 10, 55))[0] == "Break"
    assert tt.current_state(at(16, 15, 30))[0] == "Not in school"
    assert tt.current_state(at(17, 10))[0] == "Not in school"


def test_next_lesson_rolls_over_weekend() -> None:
    """After the last lesson on Friday the next lesson is Monday period 1."""
    tt = timetable()
    assert tt.next_lesson(at(16, 10)).subject == "Drama"
    nxt = tt.next_lesson(at(16, 14, 30))
    assert nxt.start == at(19, 8, 50)


def test_current_day_switches_after_school() -> None:
    """School day start/end refer to tomorrow's school day once today is over."""
    tt = timetable()
    assert tt.current_day(at(16, 9)).date == date(2026, 10, 16)
    assert tt.current_day(at(16, 16)).date == date(2026, 10, 19)
    assert tt.current_day(at(16, 16)).cycle_week == "B"


def test_homework() -> None:
    """Outstanding, due-on and overdue selections."""
    hw = parse_homework(load("homework.json")["result"], date(2026, 10, 16), 30)
    assert [h.id for h in hw.outstanding()] == ["101", "102", "103", "105"]
    assert [h.id for h in hw.due_on(date(2026, 10, 17))] == ["102", "103"]
    # Past items older than 30 days are dropped.
    assert {h.id for h in hw.past} == {"90", "91", "92"}
    assert [h.id for h in hw.overdue(date(2026, 10, 16), 14)] == ["90"]
    assert hw.current[1].attachments == 1


def test_html_to_text() -> None:
    """Homework descriptions are HTML."""
    html = load("homework_details.json")["result"]["homework"]["description"]
    assert html_to_text(html) == "Read chapter 3\nAnswer questions 1-4"
    assert html_to_text("") is None


def test_catering() -> None:
    """Balance, purchases, top-ups and spend."""
    c = parse_catering(load("catering.json")["result"])
    assert c.balance == 11.0
    assert [t.when.day for t in c.transactions] == [16, 15, 12, 9]
    assert c.purchases[0].total == -2.8
    assert c.purchases[0].items[0].name == "PIZZA SLICE"
    assert c.top_ups[0].total == 15
    assert c.spent_since(at(16, 0)) == 2.8
    assert c.spent_since(at(12, 0)) == 6.2
