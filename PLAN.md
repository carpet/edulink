# Edulink One for Home Assistant: integration plan

A HACS-installable custom integration (`custom_components/edulink`) that logs in to Edulink One as a parent (or learner), creates one device per child, and exposes homework, timetable and catering as sensors, binary sensors, a calendar and a to-do list. Cloud polling over Edulink's JSON-RPC 2.0 API using HA's shared aiohttp session. Target: HA 2026.10 (current local instance is 2026.10.1, HACS already installed, `calendar` and `todo` loaded).

All API behaviour below was verified live on 2026-10-16 against one school (parent account, one child) unless marked [unverified].

## 1. API findings

### Transport

- Provisioning: `POST https://provisioning.edulinkone.com/?method=School.FromCode`, params `{"code": "<postcode or school code>"}`. Returns `school.school_id` (int) and `school.server`, which now **already ends in `/api/`** (for example `https://wwwN.edulinkone.com/api/`). The wrapper appends `/api/?method=` itself; build the URL as `server + "?method=" + method` instead.
- All other calls: `POST {server}?method=EduLink.<Method>` with header `X-API-Method: EduLink.<Method>`, body `{"jsonrpc":"2.0","id":"1","uuid":"<uuid4>","method":...,"params":{...}}`, and `Authorization: Bearer <authtoken>` once logged in.
- **Errors always come back as HTTP 200** with `result.success = false` and `result.error` text. Never rely on HTTP status. Observed error strings:
  - `You are not logged in`: token expired or invalid, re-login.
  - `Authtoken not present (or empty) in params`: no token sent.
  - `You are not authorised to carry out this action`: method not permitted for this user type/school, or missing `learner_id`.
  - `Invalid API parameter: ...`: bad or missing parameter.
  - `Invalid API method EduLink.X`: unknown method.
  - Bad-credential login message not captured (I did not attempt a wrong password to avoid lockout) [unverified].

### Session

- Login no longer returns `session.expires`. `EduLink.Status` returns `session.expires` in seconds (~7200) and **resets it on every call**, so the session is sliding, about 2 hours idle.
- `EduLink.Ping` is refused for parents ("not authorised"), so the wrapper's keep-alive does not work. Not needed: any poll inside 2h keeps the session alive, and the client re-logs in on `You are not logged in`.
- `EduLink.Logout` works and invalidates the token.

### Endpoint compatibility with the TypeScript wrapper

| Method | Status | Change vs wrapper |
|---|---|---|
| School.FromCode | OK | `server` includes `/api/` |
| EduLink.SchoolDetails | OK | needs `establishment_id`; returns `idp_only`, `idp_login` (Microsoft SSO), base64 PNG `logo` |
| EduLink.Login | OK | parent login returns `children[]`; `user.types = ["parent"]`; `learner_menu[]` lists enabled modules; no `session` block; extra `ui_info`, `fcm_token_old` params sent by web client, not required [unverified whether required] |
| EduLink.Timetable | OK, schema changed | see below |
| EduLink.Homework | **Fails without `learner_id`** for parents | add `learner_id`; schema extended |
| EduLink.HomeworkDetails | **Fails without `learner_id`** | add `learner_id`; returns HTML `description` |
| EduLink.HomeworkCompleted | Not tested (write call) | [unverified] |
| EduLink.Catering | OK, schema changed | `balance` int; transactions lost `id` and per-transaction `balance` |
| EduLink.Status | OK, schema changed | no upcoming-lesson info any more; now counts of new messages/forms/noticeboard items plus session expiry |
| EduLink.Ping | Refused for parent | drop |
| EduLink.Exams | Refused | not in this school's menu; may work elsewhere |
| EduLink.Attendance / Behaviour / Achievement / Clubs / Noticeboard / Documents / Calendar / ExternalLinks / RegisterCodes / AchievementBehaviourLookups | OK | not core scope, see section 3 |
| EduLink.Medical | OK with `format: 2` | excluded deliberately (health data) |
| Catering menu | No method found | tried `CateringMenu`, `Menu`, `CateringMenus`: all "Invalid API method" |

### Timetable (`EduLink.Timetable`, params `date` (required, `YYYY-MM-DD`), `learner_id`)

```
result.date_range: { "<opaque id>": {start, end}, info: "Not for UI use" }
result.weeks[]: { name: "Week commencing Monday 12 October 2026",
  days[]: { date: "YYYY-MM-DD", name: "AMon", cycle_day_id: str, is_current?: true,
    periods[]: { id: str, name: "AMon:1", start_time: "HH:MM", end_time: "HH:MM" },
    lessons[]: { period_id: str, room?: {name}, teachers: str,
                 teaching_group: {name, subject}, teacher: {title} } } }
```

- Returns from Monday of the requested week for about five weeks (12 Oct to 15 Nov here). **Holiday weeks are omitted entirely** (half-term week of 26 Oct absent), so "no day entry" means no school.
- Requested for a weekend or holiday date, it starts at the next school week instead (verified 2026-10-16 for a Saturday, a Sunday, a half-term date and a Christmas date).
- `requested_date`, `showing_from/to`, `start_of_week`, `day_of_week`, numeric ids, `room.id`, `teaching_group.id` and teacher forename/surname are gone. All ids are strings. Teacher names only appear in the `teachers` free-text string.
- `is_current` is present only on today, absent (not false) elsewhere.
- `lessons` are **not ordered**: join on `period_id` to `periods` and sort by `start_time`.
- Registration appears as a lesson whose `subject` is the form group name (`AFri:Reg`). Treat periods named `*:Reg` as registration, not a lesson.
- `room` is missing on some lessons (4 of 120).
- Day names carry the week of a two-week cycle (`AMon`, `BMon`), giving a "Week A/B" sensor. Format may differ at other schools [unverified].

### Homework (`EduLink.Homework`, params `format: 2`, `learner_id`)

```
result.homework.current[] / past[]: { id: str, activity, subject, due_date: "YYYY-MM-DD",
  available_date: "YYYY-MM-DD HH:MM:SS", due_text: "In 5 days"|"today", available_text,
  completed: bool, status: "Not submitted"|"Submitted", set_by, source: "EduLink",
  format: int, duration: int|null, icon, owner_id, due_reminder, cloneable, deletable,
  attachments[]: {id, filename, filesize, mime_type, uploaded} }
```

- `current` held 12 items, `past` 463 going back to 2023. Past must never be put in state attributes (recorder 16 KB limit). Only aggregate it.
- `completed` and `status` appear to track the same thing (all `completed: true` are `Submitted` in this data). Many past items show `Not submitted`, so "overdue" based on status is likely noisy and should be opt-in.
- `description` is only available via `HomeworkDetails` and is HTML. Fetch for current items only, cache by id, strip to text.

### Catering (`EduLink.Catering`, params `learner_id`)

```
result.balance: int | float
result.transactions[]: { date: "YYYY-MM-DD HH:MM:SS", items[]: { item: str (trailing spaces), price: float } }
```

- Item prices are pounds as floats, purchases negative; top-ups appear as positive items named `PC Revaluer`.
- **`balance` is a number in pounds**: an integer for a whole-pound balance (`11` when the app showed £11.00) and a float otherwise (a second account returned `13.5` on 2026-10-17). Pence are not dropped. The £13.50 figure was not compared against the app.
- Transactions covered about four weeks (14 entries). The window size is [unverified].
- No menu data exists in the API.

### Other useful signals

- `EduLink.Status`: `new_messages`, `new_forms`, `noticeboard.new_items`, `noticeboard.new_snippets`, `moments.students[].new_items`. Cheap, and doubles as session keep-alive.
- `EduLink.Calendar`: here contains only homework due dates (`source: EduLink-Homework`), no term dates or INSET days. Redundant with Homework for this school; other schools may publish events [unverified].
- `learner_menu` ids from login tell us which modules the school enables (here: timetable, homework, catering, attendance, behaviour, achievement, calendar, clubs, noticeboard, documents, forms, parentevent, payments_ipayimpact, and others). Use it to decide which platforms/entities to create.

### SSO

`SchoolDetails.idp_only` is false here, but schools with Microsoft-only sign-in (`idp_only: true`) cannot use username/password. Out of scope for v1; config flow detects it and aborts with a clear message.

## 2. Proposed entities (per child device)

Sections 2 to 4 are the original plan. Section 6 records where v0.1.1 differs.

Device: `<Child forename>` (model "Edulink One", manufacturer = school name, entity_picture from school logo optional). Unique ids: `{establishment_id}_{learner_id}_{key}`. Names use `has_entity_name`.

### Homework

| Entity | Type | State | Key attributes |
|---|---|---|---|
| Homework outstanding | sensor (count) | current items not completed | list of `{subject, activity, due_date, set_by, status}` |
| Homework due today | sensor (count) | | titles |
| Homework due tomorrow | sensor (count) | | titles, for "evening reminder" automations |
| Next homework due | sensor (`device_class: date`) | earliest due date of outstanding | subject, activity, set_by, description (text) |
| Homework | todo | one item per current homework, `due` = due_date, description = stripped HTML | Phase 2: ticking an item calls `HomeworkCompleted` (untested write; ship read-only first) |
| Homework | calendar | all-day events on due dates (current, plus past 30 days) | |
| Overdue homework | sensor (count) | disabled by default | past, not completed, due in last N days |

### Timetable

Current/next lesson are computed locally from cached timetable data with a per-minute time listener, not extra API calls.

| Entity | Type | State | Key attributes |
|---|---|---|---|
| Current lesson | sensor | subject or "Registration", "Break", "Not in school" | room, teacher, period, start, end, group |
| Next lesson | sensor | subject | room, teacher, start (timestamp) |
| School today | binary_sensor | a timetable day exists for today | first/last period times |
| School tomorrow | binary_sensor | next calendar day has a timetable day | for alarm and bag-packing automations |
| First lesson today / Next school day start | sensor (`timestamp`) | start of first period | useful for wake-up/heating automations |
| School day end | sensor (`timestamp`) | end of last period | pickup reminders |
| Lessons tomorrow | sensor (count) | | ordered list of `{period, start, subject, room}`; enables "PE tomorrow, pack kit" |
| Timetable week | sensor (enum) | "A" / "B" (from day-name prefix) | cycle_day_id |
| Next school day | sensor (`date`) | next date with a timetable entry | detects holidays/half-term automatically |
| Timetable | calendar | one event per lesson | |

### Catering

| Entity | Type | State | Key attributes |
|---|---|---|---|
| Catering balance | sensor (`monetary`, GBP, `state_class: total`) | `balance` (see integer caveat) | |
| Low catering balance | binary_sensor | balance below options threshold (default £5) | threshold |
| Last purchase | sensor (`timestamp`) | time of latest negative transaction | items, total |
| Spent today | sensor (`monetary`) | sum of today's purchases | items |
| Spent this week | sensor (`monetary`) | Monday-to-date | |
| Last top-up | sensor (`timestamp`) | latest `PC Revaluer` | amount |

### Optional (disabled by default, created only if module is in `learner_menu`)

- New messages, new forms, new noticeboard items (from Status).
- Achievement points this academic year (sum of `points`).
- Behaviour events this academic year (count).
- Attendance: the statutory block returns per-month counts (`present`, `unauthorised`, `absent`). Their units and how a percentage should be derived are [unverified]; confirm against the attendance figure shown in the Edulink app before exposing a percentage.
- Next club session (`clubs[].next_session`).

Deliberately excluded: Medical, Personal, Documents (personal data with no automation value). Diagnostics must redact names, ids, teacher names, tokens and credentials.

### Services

- `edulink.refresh` (force refresh, per config entry).
- Phase 2: `edulink.set_homework_completed` (if HomeworkCompleted verified).

## 3. Architecture

```
custom_components/edulink/
  __init__.py        setup/unload, entry.runtime_data, platform forwarding
  api.py             async client (no HA imports): login, rpc(), typed parse to dataclasses
  models.py          dataclasses: Child, Lesson, Period, SchoolDay, Homework, CateringTxn
  coordinator.py     three DataUpdateCoordinators per entry (see polling)
  config_flow.py     user, child selection, reauth, reconfigure, options
  entity.py          base CoordinatorEntity with device_info per child
  sensor.py, binary_sensor.py, calendar.py, todo.py
  diagnostics.py     redacted dump
  const.py, manifest.json, strings.json, translations/en.json, icons.json, services.yaml
hacs.json, README.md, LICENSE, .github/workflows/{validate,release}.yml, tests/
```

Keeping `api.py` free of HA imports allows later extraction to a PyPI library (`pyedulink`), which HA core would require if this were ever upstreamed.

### API client

- Uses `async_get_clientsession(hass)`; holds token in memory only.
- `rpc(method, params)`: inject `learner_id`, uuid, headers; parse `result`; map `You are not logged in` to one automatic re-login and retry; map login failure to `EdulinkAuthError`; map `not authorised` to `EdulinkPermissionError` (entity becomes unavailable, not a reauth); timeouts and network to `EdulinkConnectionError`.
- Single `asyncio.Lock` around login so concurrent coordinators do not log in twice.
- All naive date/time strings parsed as `Europe/London` (school local time), via `dt_util`.

### Config flow

1. User step: postcode or school code. Call `School.FromCode`, then `SchoolDetails`; show school name for confirmation; abort if `idp_only`.
2. Credentials step: username, password. Login. Unique id `{establishment_id}_{user.id}`.
3. Children step (parent accounts with more than one child): multi-select, default all. Learner logins use `user.id` directly.
4. Reauth flow on `ConfigEntryAuthFailed`. Reconfigure flow for credential change.
5. Options: polling intervals, low-balance threshold, include/exclude optional modules, homework overdue window.

Credentials are stored in the config entry (required, since sessions expire after ~2h idle). Note this in the README.

### Polling (per entry, all children batched in one cycle)

| Coordinator | Calls | Interval |
|---|---|---|
| Timetable | Timetable (date = today) | 6 h, plus at 00:05 daily |
| Homework | Homework, HomeworkDetails for new ids only, Status | 30 min, 2 h overnight 22:00 to 06:00 |
| Catering | Catering | 15 min 07:30 to 16:00 on school days, otherwise 2 h |

About 100 to 150 requests per day per child. Respectful of a service with no published rate limits; adjustable in options.

### Comparison with the ACIT integration

Reused patterns: JSON-RPC envelope, `DataUpdateCoordinator`, config flow with reauth, HACS layout (`content_in_root: false`), `hacs/action` plus `hassfest` plus ruff CI, release workflow.

Differences: Edulink is `iot_class: cloud_polling` and `integration_type: service` (no zeroconf, no websocket). Use HA's shared session rather than ACIT's own `aiohttp.ClientSession()` (ACIT never closes it), `entry.runtime_data` rather than `hass.data[DOMAIN]`, and services registered once in `async_setup` rather than per entry. Edulink errors arrive as HTTP 200, unlike ACIT's `result.error` / HTTP 401 handling.

### manifest.json

```json
{
  "domain": "edulink",
  "name": "Edulink One",
  "codeowners": ["@carpet"],
  "config_flow": true,
  "documentation": "https://github.com/carpet/edulink",
  "issue_tracker": "https://github.com/carpet/edulink/issues",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "requirements": [],
  "version": "0.1.0"
}
```

No external requirements (aiohttp ships with HA).

## 4. Delivery phases

1. **API client and fixtures.** `api.py`, `models.py`, parsers. Capture fresh responses and sanitise them (replace names, ids, teacher names, room names, items) into `tests/fixtures/`. Unit tests for parsing, lesson ordering, holiday-week gaps, re-login on expiry.
2. **Config flow and coordinators.** User, child selection, reauth, options. `pytest-homeassistant-custom-component` tests for each flow step and error.
3. **Core entities.** Homework, timetable and catering sensors and binary sensors; diagnostics.
4. **Calendar and to-do (read-only).**
5. **HACS packaging.** `hacs.json`, README with screenshots and example automations (homework due tomorrow at 18:00, low balance alert, PE kit reminder, school-tomorrow alarm), CI, tagged release. Install via HACS custom repository on 192.168.1.205.
6. **Phase 2.** Verify `HomeworkCompleted` on a disposable homework item, then enable to-do write-back. Optional modules. Multi-school/multi-account testing.

## 5. Decisions (2026-10-16)

1. Catering balance confirmed as pounds: integer for whole pounds (£11.00), float with pence (13.5 seen on 2026-10-17).
2. Read-only for now. `HomeworkCompleted` is not called; the to-do list is read-only.
3. Repository `carpet/edulink` on GitHub.
4. Attendance, behaviour and achievement deferred. See `HANDOVER.md` for how to add them.

## 6. As built (v0.1.1)

Differences from sections 2 to 4:

- No `edulink.refresh` service or `services.yaml`; `homeassistant.update_entity` covers it.
- No reconfigure flow and no way to change the selected children without re-adding the entry (deferred).
- Options are the low-balance threshold and the homework and catering intervals only. No module include/exclude or overdue-window option; the overdue window is fixed at 14 days.
- Homework polling does not call `EduLink.Status`.
- Device manufacturer is "Edulink One" and the model is the school name.
- Current lesson attributes are room, period, start and end. Teacher names and homework `set_by` are kept out of sensor attributes and appear only in calendar event descriptions.
- Timetable week is a plain sensor (week letter), without `cycle_day_id` or an enum device class.
- Times are parsed with `zoneinfo` (Europe/London) in `models.py`, not `dt_util`, to keep that module free of Home Assistant imports.
- Polling: timetable every 6 hours and at school-time midnight; homework every 30 minutes, capped so it slows exactly at 22:00 and speeds up at 06:00; catering every 15 minutes 07:30 to 16:00 on school days (holidays from the timetable), otherwise up to 2 hours, waking in time for the next opening. Any failed update retries within 15 minutes.
- Per-learner failures are isolated; timetable entities stay available on cached data that covers today.

