"""Parsed Edulink data and the pure functions that derive sensor values.

No Home Assistant imports: everything takes an explicit "now" so it can be
unit tested without a running instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
import re
from typing import Any
from zoneinfo import ZoneInfo

# Edulink is UK-only and returns naive local times.
SCHOOL_TZ = ZoneInfo("Europe/London")

TOP_UP_ITEM = "PC Revaluer"
_CYCLE_WEEK = re.compile(r"^([A-Z])(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=SCHOOL_TZ)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str | None) -> str | None:
    """Strip Edulink's HTML homework descriptions to plain text."""
    if not html:
        return None
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return text or None


# ---------------------------------------------------------------- timetable


@dataclass(slots=True, frozen=True)
class Lesson:
    """One timetabled period, including registration."""

    period: str
    start: datetime
    end: datetime
    subject: str
    group: str
    room: str | None
    teacher: str | None
    is_registration: bool

    def as_dict(self) -> dict[str, Any]:
        """Attribute-friendly representation."""
        return {
            "period": self.period,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "subject": self.subject,
            "room": self.room,
            "teacher": self.teacher,
        }


@dataclass(slots=True, frozen=True)
class SchoolDay:
    """A day with a timetable. Days absent from the timetable have no school."""

    date: date
    name: str
    cycle_day_id: str | None
    lessons: tuple[Lesson, ...]

    @property
    def start(self) -> datetime | None:
        """Start of the first period, including registration."""
        return self.lessons[0].start if self.lessons else None

    @property
    def end(self) -> datetime | None:
        """End of the last period."""
        return max(lesson.end for lesson in self.lessons) if self.lessons else None

    @property
    def teaching(self) -> list[Lesson]:
        """Lessons excluding registration."""
        return [lesson for lesson in self.lessons if not lesson.is_registration]

    @property
    def cycle_week(self) -> str | None:
        """Week letter of a multi-week cycle, from day names like "AMon"."""
        match = _CYCLE_WEEK.match(self.name)
        return match.group(1) if match else None


@dataclass(slots=True)
class Timetable:
    """All days returned by EduLink.Timetable, keyed by date."""

    days: dict[date, SchoolDay] = field(default_factory=dict)

    def day(self, on: date) -> SchoolDay | None:
        """The school day on a date, if any."""
        return self.days.get(on)

    def next_school_day(self, after: date) -> SchoolDay | None:
        """First school day strictly after a date."""
        upcoming = [d for d in self.days if d > after]
        return self.days[min(upcoming)] if upcoming else None

    def current_day(self, now: datetime) -> SchoolDay | None:
        """Today if school has not finished yet, otherwise the next school day."""
        today = self.day(now.date())
        if today and today.end and now < today.end:
            return today
        return self.next_school_day(now.date())

    def current_state(self, now: datetime) -> tuple[str, Lesson | None]:
        """What the learner is timetabled to be doing right now."""
        today = self.day(now.date())
        if not today or not today.start or not today.end:
            return "Not in school", None
        if now < today.start or now >= today.end:
            return "Not in school", None
        for lesson in today.lessons:
            if lesson.start <= now < lesson.end:
                if lesson.is_registration:
                    return "Registration", lesson
                return lesson.subject, lesson
        return "Break", None

    def next_lesson(self, now: datetime) -> Lesson | None:
        """The next non-registration lesson starting after now."""
        for day in sorted(d for d in self.days if d >= now.date()):
            for lesson in self.days[day].teaching:
                if lesson.start > now:
                    return lesson
        return None


def parse_timetable(result: dict[str, Any]) -> Timetable:
    """Parse an EduLink.Timetable result."""
    timetable = Timetable()
    for week in result.get("weeks") or []:
        for raw_day in week.get("days") or []:
            day = _parse_date(raw_day.get("date"))
            if day is None:
                continue
            periods = {str(p.get("id")): p for p in raw_day.get("periods") or []}
            lessons: list[Lesson] = []
            for raw in raw_day.get("lessons") or []:
                period = periods.get(str(raw.get("period_id")))
                if not period:
                    continue
                start = _combine(day, period.get("start_time"))
                end = _combine(day, period.get("end_time"))
                if start is None or end is None:
                    continue
                name = str(period.get("name") or "")
                group = raw.get("teaching_group") or {}
                room = (raw.get("room") or {}).get("name")
                lessons.append(
                    Lesson(
                        period=name.split(":", 1)[-1],
                        start=start,
                        end=end,
                        subject=str(group.get("subject") or ""),
                        group=str(group.get("name") or ""),
                        room=str(room) if room else None,
                        teacher=raw.get("teachers") or None,
                        is_registration=name.lower().endswith(":reg"),
                    )
                )
            lessons.sort(key=lambda lesson: lesson.start)
            timetable.days[day] = SchoolDay(
                date=day,
                name=str(raw_day.get("name") or ""),
                cycle_day_id=raw_day.get("cycle_day_id"),
                lessons=tuple(lessons),
            )
    return timetable


def _combine(day: date, hhmm: str | None) -> datetime | None:
    if not hhmm:
        return None
    try:
        return datetime.combine(day, time.fromisoformat(hhmm), SCHOOL_TZ)
    except ValueError:
        return None


# ----------------------------------------------------------------- homework


@dataclass(slots=True)
class Homework:
    """One homework item."""

    id: str
    subject: str
    activity: str
    due: date
    set_on: datetime | None
    set_by: str
    status: str
    completed: bool
    source: str
    attachments: int
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Attribute-friendly representation."""
        return {
            "subject": self.subject,
            "activity": self.activity,
            "due": self.due.isoformat(),
            "set_by": self.set_by,
            "status": self.status,
        }


@dataclass(slots=True)
class HomeworkList:
    """Current homework, plus recent past homework."""

    current: list[Homework] = field(default_factory=list)
    past: list[Homework] = field(default_factory=list)

    def outstanding(self) -> list[Homework]:
        """Current homework not marked complete, soonest first."""
        return sorted(
            (hw for hw in self.current if not hw.completed), key=lambda hw: hw.due
        )

    def due_on(self, day: date) -> list[Homework]:
        """Outstanding homework due on a date."""
        return [hw for hw in self.outstanding() if hw.due == day]

    def overdue(self, today: date, window_days: int) -> list[Homework]:
        """Incomplete homework due within the last window_days."""
        since = today - timedelta(days=window_days)
        return sorted(
            (
                hw
                for hw in (*self.current, *self.past)
                if not hw.completed and since <= hw.due < today
            ),
            key=lambda hw: hw.due,
        )


def parse_homework(result: dict[str, Any], today: date, past_days: int) -> HomeworkList:
    """Parse an EduLink.Homework result, keeping only recent past items."""
    data = result.get("homework") or {}
    since = today - timedelta(days=past_days)
    current = [hw for raw in data.get("current") or [] if (hw := _homework(raw))]
    past = [
        hw
        for raw in data.get("past") or []
        if (hw := _homework(raw)) and hw.due >= since
    ]
    return HomeworkList(current=current, past=past)


def _homework(raw: dict[str, Any]) -> Homework | None:
    due = _parse_date(raw.get("due_date"))
    if raw.get("id") is None or due is None:
        return None
    return Homework(
        id=str(raw["id"]),
        subject=str(raw.get("subject") or ""),
        activity=str(raw.get("activity") or ""),
        due=due,
        set_on=_parse_dt(raw.get("available_date")),
        set_by=str(raw.get("set_by") or ""),
        status=str(raw.get("status") or ""),
        completed=bool(raw.get("completed")),
        source=str(raw.get("source") or "EduLink"),
        attachments=len(raw.get("attachments") or []),
    )


# ----------------------------------------------------------------- catering


@dataclass(slots=True, frozen=True)
class CateringItem:
    """One line of a catering transaction. Purchases are negative."""

    name: str
    price: float


@dataclass(slots=True, frozen=True)
class CateringTransaction:
    """A till transaction or top-up."""

    when: datetime
    items: tuple[CateringItem, ...]

    @property
    def total(self) -> float:
        """Net value; negative for a purchase."""
        return round(sum(item.price for item in self.items), 2)

    @property
    def is_top_up(self) -> bool:
        """True if this transaction credited the account."""
        return any(item.name == TOP_UP_ITEM for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        """Attribute-friendly representation."""
        return {
            "time": self.when.isoformat(),
            "items": [{"item": i.name, "price": i.price} for i in self.items],
            "total": self.total,
        }


@dataclass(slots=True)
class Catering:
    """Balance and recent transactions, newest first."""

    balance: float | None
    transactions: list[CateringTransaction] = field(default_factory=list)

    @property
    def purchases(self) -> list[CateringTransaction]:
        """Transactions that spent money."""
        return [t for t in self.transactions if not t.is_top_up and t.total < 0]

    @property
    def top_ups(self) -> list[CateringTransaction]:
        """Transactions that credited the account."""
        return [t for t in self.transactions if t.is_top_up]

    def spent_since(self, since: datetime) -> float:
        """Total spent (as a positive number) since a point in time."""
        return round(-sum(t.total for t in self.purchases if t.when >= since), 2)


def parse_catering(result: dict[str, Any]) -> Catering:
    """Parse an EduLink.Catering result.

    The API returns balance as an integer. Confirmed as pounds for a whole-pound
    balance; behaviour for a balance with pence is unverified.
    """
    balance = result.get("balance")
    transactions: list[CateringTransaction] = []
    for raw in result.get("transactions") or []:
        when = _parse_dt(raw.get("date"))
        if when is None:
            continue
        items = tuple(
            CateringItem(name=str(i.get("item") or "").strip(), price=float(i["price"]))
            for i in raw.get("items") or []
            if isinstance(i.get("price"), int | float)
        )
        transactions.append(CateringTransaction(when=when, items=items))
    transactions.sort(key=lambda t: t.when, reverse=True)
    return Catering(
        balance=float(balance) if isinstance(balance, int | float) else None,
        transactions=transactions,
    )
