"""Tests for the pure parsing and derivation functions."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.edulink.models import (
    SCHOOL_TZ,
    Lesson,
    academic_year_start,
    html_to_text,
    kit_for,
    parse_achievement,
    parse_attendance,
    parse_behaviour,
    parse_catering,
    parse_clubs,
    parse_homework,
    parse_kit_rules,
    parse_status,
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
    assert [h.id for h in hw.outstanding(date(2026, 10, 17))] == ["101", "102", "103"]
    assert [h.id for h in hw.outstanding(date(2026, 10, 16))] == ["101"]
    assert [h.id for h in hw.due_on(date(2026, 10, 17))] == ["102", "103"]
    # Past items older than 30 days are dropped.
    assert {h.id for h in hw.past} == {"90", "91", "92"}
    assert [h.id for h in hw.overdue(date(2026, 10, 16), 14)] == ["90"]
    # Urgency comes from the soonest outstanding homework within the window.
    assert hw.urgency(date(2026, 10, 16)) == "today"
    assert hw.urgency(date(2026, 10, 15)) == "tomorrow"
    assert hw.urgency(date(2026, 10, 14)) == "later"
    assert hw.urgency(date(2026, 10, 14), until=date(2026, 10, 15)) == "none"
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


def test_attributes_exclude_staff_names() -> None:
    """Teacher names stay out of state attributes (and so out of history)."""
    tt = timetable()
    lesson = tt.day(date(2026, 10, 16)).teaching[0]
    assert lesson.teacher == "Dr Teacher1"
    assert "teacher" not in lesson.as_dict()
    hw = parse_homework(load("homework.json")["result"], date(2026, 10, 16), 30)
    assert "set_by" not in hw.current[0].as_dict()


def test_timetable_coverage_from_a_holiday() -> None:
    """Requested during half-term, Edulink starts at the next school week.

    Every date from the request to the last day returned is then known.
    """
    raw = load("timetable.json")["result"]
    raw["weeks"] = raw["weeks"][2:]  # only w/c 2 November
    tt = parse_timetable(raw, date(2026, 10, 28))
    assert tt.is_school_day(date(2026, 10, 28)) is False
    assert tt.is_school_day(date(2026, 10, 30)) is False
    assert tt.is_school_day(date(2026, 11, 2)) is True
    assert tt.is_school_day(date(2026, 11, 6)) is True
    # After the last day returned nothing is known, even a weekend.
    assert tt.is_school_day(date(2026, 11, 7)) is None
    assert tt.is_school_day(date(2026, 10, 27)) is None
    assert tt.covers(date(2026, 10, 28))
    assert not tt.covers(date(2026, 11, 9))


def test_timetable_coverage_unknown_without_request_date() -> None:
    """Without the request date only listed days are known."""
    tt = timetable()
    assert tt.is_school_day(date(2026, 10, 16)) is True
    assert tt.is_school_day(date(2026, 10, 17)) is None


def test_parsers_tolerate_malformed_fields() -> None:
    """Unexpected types are skipped or coerced rather than failing the update."""
    tt = parse_timetable(
        {
            "weeks": [
                "junk",
                {"days": [None, {"date": 20261016}]},
                {
                    "days": [
                        {
                            "date": "2026-10-16",
                            "name": None,
                            "periods": [
                                {
                                    "id": 1,
                                    "name": "X:1",
                                    "start_time": "09:00",
                                    "end_time": "10:00",
                                },
                                {
                                    "id": 2,
                                    "name": "X:2",
                                    "start_time": None,
                                    "end_time": "11:00",
                                },
                                "junk",
                            ],
                            "lessons": [
                                {
                                    "period_id": "1",
                                    "teaching_group": "Maths",
                                    "room": ["R1"],
                                    "teachers": 42,
                                },
                                {
                                    "period_id": "2",
                                    "teaching_group": {"subject": "Art"},
                                },
                                {"period_id": "3"},
                                7,
                            ],
                        }
                    ]
                },
            ]
        },
        date(2026, 10, 16),
    )
    day = tt.day(date(2026, 10, 16))
    assert len(tt.days) == 1
    assert [
        (lesson.subject, lesson.room, lesson.teacher) for lesson in day.lessons
    ] == [("", None, "42")]

    hw = parse_homework(
        {
            "homework": {
                "current": [
                    {
                        "id": 1,
                        "due_date": "2026-10-20",
                        "completed": "false",
                        "attachments": "x",
                    },
                    {"id": 2, "due_date": None},
                    {"due_date": "2026-10-20"},
                    "junk",
                ],
                "past": None,
            }
        },
        date(2026, 10, 16),
        30,
    )
    assert [(h.id, h.completed, h.attachments) for h in hw.current] == [("1", False, 0)]
    assert parse_homework({"homework": []}, date(2026, 10, 16), 30).current == []

    c = parse_catering(
        {
            "balance": "11",
            "transactions": [
                {
                    "date": "2026-10-16 12:00:00",
                    "items": [
                        {"item": None, "price": -1},
                        {"item": "X", "price": "2"},
                        {"item": "Y", "price": True},
                    ],
                },
                {"date": None, "items": []},
                "junk",
            ],
        }
    )
    assert c.balance is None
    assert [(i.name, i.price) for t in c.transactions for i in t.items] == [("", -1.0)]
    assert parse_catering({"balance": True}).balance is None


def _lesson(subject: str) -> Lesson:
    start = datetime(2026, 10, 19, 9, tzinfo=SCHOOL_TZ)
    return Lesson(
        period="1",
        start=start,
        end=start + timedelta(hours=1),
        subject=subject,
        group="10A",
        room=None,
        teacher=None,
        is_registration=False,
    )


def test_kit_rules() -> None:
    """Keywords match whole words, ignoring case; items are not repeated."""
    rules = parse_kit_rules(
        "Physical Education = PE kit\n\nPE = PE kit\n P.E. = PE kit \nArt = Art materials"
    )
    lessons = [
        _lesson(s)
        for s in (
            "Particle Physics",
            "physical education",
            "Art & Design",
            "PE",
            "P.E.",
        )
    ]
    assert kit_for(lessons, rules) == ["PE kit", "Art materials"]
    assert kit_for([_lesson("Particle Physics"), _lesson("Pending")], rules) == []
    assert parse_kit_rules("") == []


@pytest.mark.parametrize("text", ["PE kit", "= PE kit", "PE =", "PE = PE kit\nArt"])
def test_kit_rules_invalid(text: str) -> None:
    """A line needs a keyword and an item."""
    with pytest.raises(ValueError):
        parse_kit_rules(text)


@pytest.mark.parametrize(
    ("today", "month", "expected"),
    [
        (date(2026, 10, 16), 9, date(2026, 9, 1)),
        (date(2026, 9, 1), 9, date(2026, 9, 1)),
        (date(2026, 8, 31), 9, date(2025, 9, 1)),
        (date(2026, 1, 5), 1, date(2026, 1, 1)),
    ],
)
def test_academic_year_start(today: date, month: int, expected: date) -> None:
    """The academic year starts on the 1st of the configured month."""
    assert academic_year_start(today, month) == expected


def test_achievement() -> None:
    """Dates with or without a time, string points, newest first."""
    a = parse_achievement(load("achievement.json")["result"])
    assert [e.date.isoformat() for e in a.events] == [
        "2026-10-15",
        "2026-10-12",
        "2026-09-10",
        "2026-07-01",
    ]
    assert a.latest == date(2026, 10, 15)
    assert a.points_since(date(2026, 9, 1)) == 10
    assert len(a.since(date(2026, 10, 12))) == 2


def test_behaviour() -> None:
    """Behaviour events are sorted and carry no points."""
    b = parse_behaviour(load("behaviour.json")["result"])
    assert b.latest == date(2026, 10, 14)
    assert len(b.since(date(2026, 9, 1))) == 2
    assert b.points_since(date(2000, 1, 1)) == 0


def test_attendance() -> None:
    """Only absences count, across every month returned."""
    a = parse_attendance(load("attendance.json")["result"])
    assert a.absences == [date(2026, 10, 5), date(2026, 9, 15)]
    assert a.last_absence == date(2026, 10, 5)


def test_clubs() -> None:
    """Deleted clubs are skipped; the next session is the earliest still to come."""
    c = parse_clubs(load("clubs.json")["result"])
    assert c.names == ["Chess Club", "Choir"]
    session = c.next_session(at(16, 10))
    assert session.club == "Chess Club"
    assert session.start == at(20, 15, 15)
    assert c.next_session(at(21, 10)) is None


def test_status() -> None:
    """Account-wide unread counts."""
    s = parse_status(load("status.json")["result"])
    assert (s.new_messages, s.new_forms, s.new_noticeboard_items) == (2, 1, 3)


def test_record_parsers_tolerate_malformed_fields() -> None:
    """Unparseable dates are skipped and odd points count as zero.

    Whole-number decimals such as 4.0 count as their value.
    """
    a = parse_achievement(
        {
            "achievement": [
                {"date": "16/10/2026", "points": "x"},
                {"date": None, "points": 5},
                {"date": "2026-10-15", "points": True},
                {"date": "2026-10-14", "points": 2.5},
                {"date": "2026-10-13", "points": 4.0},
                "junk",
            ]
        }
    )
    assert [(e.date, e.points) for e in a.events] == [
        (date(2026, 10, 16), 0),
        (date(2026, 10, 15), 0),
        (date(2026, 10, 14), 0),
        (date(2026, 10, 13), 4),
    ]
    assert parse_behaviour({"behaviour": None}).latest is None
    assert parse_attendance({"statutory": [None, {"exceptions": "x"}]}).absences == []
    assert (
        parse_attendance(
            {"statutory": [{"exceptions": [{"type": "ABSENT", "date": "bad"}]}]}
        ).absences
        == []
    )
    clubs = parse_clubs(
        {
            "clubs": [
                {"name": None, "next_session": 5},
                {"name": "X", "next_session": ""},
            ]
        }
    )
    assert (clubs.names, clubs.sessions) == (["X"], [])
    assert parse_status({"noticeboard": [], "new_messages": "2"}) == parse_status(
        {"new_messages": 2}
    )
