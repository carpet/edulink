# Handover: adding further Edulink modules

v0.1.0 covered timetable, homework and catering, read-only. This document records what was observed for the other modules and the steps to add one, following the pattern the existing modules use.

## Status of the modules below (v0.1.4)

Built, following the rules in "Why they were left out":

- Achievement: points this academic year (a total reset on the 1st of the start month option, default September), achievements this week, last achievement date. Disabled by default.
- Behaviour: events this academic year, last event date. Disabled by default.
- Attendance: last absence date (latest `statutory[].exceptions[]` with `type: "Absent"`). Disabled by default.
- Clubs: next club session timestamp, with club names as attributes. Enabled.
- Status: new messages, new forms and new noticeboard items, on an account-level "Edulink One" device. Enabled. Created unless `EduLink.Status` returns "not authorised", since it is not listed in `learner_menu`.

The record modules and clubs share `RecordCoordinator` (one call per learner, 12-hour interval); Status has its own `StatusCoordinator`. Parsers accept ISO dates with or without a time, and UK `dd/mm/yyyy`, because these formats were not confirmed against live data. If a field arrives in another format the entity shows unknown rather than failing.

Still not built, pending the [unverified] points below: attendance counts or percentage, "marked absent today", detentions and report cards, behaviour and achievement type names (lookups), `RegisterCodes`. Checked on a live account (two parent accounts, one school) on 2026-10-17 with v0.1.4b1: achievement, behaviour and attendance exception dates all parsed; achievement, behaviour, absence, message and noticeboard counts matched the Edulink app; `clubs[].next_session` parses as a local `YYYY-MM-DD HH:MM:SS` time; and reading messages in the app clears `new_messages`. Achievement points this year also matched the app. Attendance absence dates matched the app for the academic year so far; in October that is the two months returned (September and October). Whether Edulink returns every month of the academic year later in the year, or only the current and previous month, is [unverified]; check in November or later.

Observations come from one parent account at one school on 2026-10-16. Anything not seen directly is marked [unverified]. Before building on a schema below, capture a fresh response, because Edulink changes without notice (the TypeScript wrapper's schemas were already out of date).

## Why they were left out

Attendance, behaviour and achievement describe a child's school record rather than logistics. That raises privacy questions (Home Assistant history keeps state indefinitely, other household members can see it, and it ends up in backups). It also adds little automation value beyond notifications. If they are added:

- Create them with `entity_registry_enabled_default=False` so nothing is recorded until someone opts in.
- Keep attributes to aggregates. Do not put staff comments, incident descriptions or staff names into state attributes; the existing modules follow the same rule (teacher names appear only in calendar event descriptions, which Home Assistant excludes from the recorder).
- Keep diagnostics to counts, as now (`diagnostics.py`, enforced by `test_diagnostics_have_no_personal_data`).
- Never add `EduLink.Medical`, `EduLink.Personal` or `EduLink.Documents`. They return health and contact details, and there is no automation use for them.

## Recipe for a new module

Use catering as the template: one endpoint, one coordinator, a handful of sensors.

1. **Module id.** Add `MODULE_<NAME>` to `const.py` with the id from the login response's `learner_menu` (for example `attendance`, `behaviour`, `achievement`, `clubs`). Setup only creates a coordinator if the id is in the menu, so schools without the module get no entities.
2. **API method.** Add a method to `EdulinkClient` in `api.py` that calls `self.call("EduLink.<Method>", {...})`. Always pass `learner_id`, because parent accounts get "You are not authorised" without it. `call()` already handles session expiry and error mapping.
3. **Model and parser.** In `models.py`, add a dataclass and a `parse_<name>(result)` function, plus any derived values as methods that take an explicit `today`/`now`. Keep this file free of Home Assistant imports and parse defensively (`.get()` with defaults), because fields come and go.
4. **Coordinator.** Subclass `EdulinkCoordinator[YourModel]` in `coordinator.py` and implement `_fetch(learner_id)` and `_interval(now)`. These modules change slowly; an interval of 6 to 12 hours is enough. The base class already handles per-learner failures (a failing learner keeps its last data, the update only fails if every learner fails) and retries within `RETRY_INTERVAL` after any failure. Override `is_usable` only if entities should stay available on stale data, as the timetable does. Add a field for it on `EdulinkRuntimeData`.
5. **Setup.** In `__init__.py`, create the coordinator when the module is in `modules`, and add it to the first-refresh loop. If any sensor depends on the date ("this week", "today"), add the coordinator to the midnight rollover in `async_start_clock`.
6. **Entities.** In `sensor.py`, add a tuple of `EdulinkSensorDescription[YourModel]` and add `(runtime.<name>, YOUR_SENSORS)` to the loop in `async_setup_entry`. Unique ids and devices are handled by `EdulinkEntity`.
7. **Strings and icons.** Add entity names under `entity.sensor.<translation_key>` in `strings.json`, regenerate `translations/en.json` (copy, with `[%key:...]` references expanded), and add icons in `icons.json`.
8. **Diagnostics.** Add counts only.
9. **Tests.** Add a synthetic fixture to `tests/fixtures/`, route it in `FakeEdulink.responses` (`tests/conftest.py`; use `learner_responses` for per-child data), add parser tests including malformed fields to `test_models.py`, and entity tests to `test_init.py` or `test_runtime.py`. Extend `test_module_gating` and the two-learner tests.
10. **Docs.** README entity table and privacy section.

## Observed endpoints

### Attendance: `EduLink.Attendance`

Params: `learner_id`, `format: 3` (the wrapper used 3; other values untested).

```
result.show_today: bool, show_lesson: bool, show_statutory: bool
result.lesson: []                     (empty at this school; show_lesson false)
result.statutory[]: {
  month: "YYYY-MM",
  values: { present: int, unauthorised: int, absent: int },
  exceptions[]: { date, description, type: "Absent" | "Present", period }
}
```

- Two months were returned (the current one and the previous). Whether more history is available, for example by parameter, is [unverified].
- The units of `values` (sessions, half-days or marks) and how the three counts relate (whether `absent` means authorised absence, whether `present + unauthorised + absent` is the total) are [unverified]. To confirm: compare against the attendance percentage shown in the Edulink app for the same month. Do not publish a percentage sensor until this is confirmed.
- The format of `exceptions[].date` and `period` was not inspected [unverified].
- `RegisterCodes` (`EduLink.RegisterCodes`, no params) returns the school's mark codes (`statutory_codes`, `lesson_codes`) and may help interpret exceptions.

Candidate entities: attendance this month (only once units are confirmed), last absence date, `binary_sensor` "marked absent today" if `show_today` is true at a school [unverified]. Sensible defaults: disabled by default, 12 h polling.

### Behaviour: `EduLink.Behaviour`

Params: `learner_id`, `format: 2`.

```
result.behaviour[]: { id, source, type_ids: [str], date, recorded: { employee_id }, lesson_information }
result.detentions: null | ...          (null here; populated schema [unverified])
result.report_cards: null | ...        (null here)
result.employees[]: { id, title, forename, surname }
result.hide_fields: [str]
```

`type_ids` resolve through `EduLink.AchievementBehaviourLookups` (no params), which returns lookup tables. Keys seen include `behaviour_activity_types`, `behaviour_actions_taken`, `behaviour_bullying_types` and `behaviour_detention_attendance`; the full key list was truncated in the probe and the table structures were not inspected [unverified].

Candidate entities: behaviour events this academic year (count), date of most recent, upcoming detention (once the `detentions` schema is seen). Do not expose `lesson_information` or staff names.

### Achievement: `EduLink.Achievement`

Params: `learner_id`.

```
result.achievement[]: { id, source, type_ids: [str], activity_id, date, recorded: { employee_id },
                        comments: str, points: int, location_id, lesson_information }
result.employees[]: { id, title, forename, surname }
result.hide_fields: []
```

Candidate entities: achievement points this academic year (`state_class: total_increasing`, reset each September), achievements this week, latest achievement date. Type names come from the same lookup endpoint (`achievement_types`, `achievement_award_types`, `achievement_activity_types`). `comments` are free text written by staff, so leave them out of attributes.

Academic year boundary: no API field was seen for term or year dates (the calendar endpoint only held homework at this school), so use 1 September as a configurable default.

### Status counts: `EduLink.Status`

Params: `last_visible: 0`. No `learner_id`.

```
result.new_messages: int, new_forms: int
result.noticeboard: { new_items: int, new_snippets: int }
result.moments.students[]: { id, new_items: int }
result.session.expires: int            (seconds; resets on every authenticated call)
```

Cheap and non-sensitive. Good candidates for "unread messages" and "new forms" sensors, which are useful for prompting a parent to open the app. "New" is relative to what the Edulink account has seen, so reading in the app clears them (confirmed for messages, 2026-10-17).

### Clubs: `EduLink.Clubs`

Params: `learner_id`, `member: true` (clubs the learner belongs to; `false` lists the others).

```
result.clubs[]: { id, name, deleted, description, capacity: { maximum, bookings }, location, next_session: str }
```

Candidate: "next club session" timestamp sensor. The `next_session` format was not inspected [unverified].

### Other methods seen

| Method | Result | Notes |
|---|---|---|
| `EduLink.Calendar` (`learner_id`, `date`) | Only homework due dates at this school (`source: EduLink-Homework`) | Redundant with homework; other schools may publish events [unverified] |
| `EduLink.Noticeboard` (`learner_id`) | File list (`name`, `filename`, `mime_type`, folder ids) | Low value |
| `EduLink.ExternalLinks` | School links | Low value |
| `EduLink.Exams` | "Not authorised" | Not enabled at this school |
| `EduLink.Ping` | "Not authorised" for parents | Not needed; any call refreshes the session |
| `EduLink.HomeworkCompleted` | Not called | Write call. Params per wrapper: `completed: "true"/"false"` (strings), `homework_id`, `learner_id`, `source`. Test on a single item and revert before enabling to-do write-back |

`learner_menu` at this school also listed `forms`, `parentevent`, `account`, `contact`, `attendance_absencereporting` and `payments_ipayimpact`; none were probed.

## Gotchas already handled in the core code

- Errors arrive as HTTP 200 with `result.success: false`; `api._post` maps them.
- `School.FromCode` returns a server URL that already ends in `/api/`; `_server_url` normalises it either way.
- Parent accounts must send `learner_id` to every learner method, including `Homework` and `HomeworkDetails`.
- Sessions slide for about two hours; `EdulinkClient.call` re-logs in once on "You are not logged in", under a lock so parallel coordinators do not log in twice.
- Times are naive Europe/London strings; parse with `models.SCHOOL_TZ`.
- Lists can be unordered (timetable lessons) and fields optional (lesson `room`). Parsers use `_dict`, `_dicts` and `_str` in `models.py` so a field of an unexpected type is skipped rather than failing the update.
- A timetable fetched for a holiday date starts at the next school week. `Timetable.covers` and `is_school_day` use the request date to treat every date up to the last day returned as known.
