# Edulink One for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub Release](https://img.shields.io/github/v/release/carpet/edulink)](https://github.com/carpet/edulink/releases)
[![Validate](https://github.com/carpet/edulink/actions/workflows/validate.yml/badge.svg)](https://github.com/carpet/edulink/actions/workflows/validate.yml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-41BDF5?logo=home-assistant)](https://www.home-assistant.io/)
[![License](https://img.shields.io/github/license/carpet/edulink)](LICENSE)

Unofficial Home Assistant integration for [Edulink One](https://www.edulinkone.com/), the UK school parent/learner portal. Provides homework, timetable and catering sensors, plus calendars and a homework to-do list, for each child on a parent account (or for a learner's own account).

Not affiliated with or endorsed by Edulink One or its developers. Edulink has no public API: this integration uses the same JSON-RPC calls as the Edulink website and may break if they change.

## Installation (HACS)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=carpet&repository=edulink&category=integration)

Use the button above, or add the repository manually:

1. HACS, three-dot menu, **Custom repositories**, add `https://github.com/carpet/edulink` as type **Integration**.
2. Install **Edulink One** and restart Home Assistant.
3. **Settings, Devices & services, Add integration, Edulink One**.
4. Enter your school's postcode or Edulink school code, then your Edulink username and password. Parent accounts with several children can choose which to add.

Schools that only allow Microsoft sign-in are not supported.

Your Edulink username and password are stored in Home Assistant's config entry storage. They are needed because Edulink sessions expire after about two hours idle.

## Entities

One device per child. Entities are only created for modules your school has enabled.

**Timetable** (updated every 6 hours and at midnight; time-based states recalculated on the minute. If an update fails, entities keep using the cached timetable while it still covers today)

| Entity | Description |
|---|---|
| `sensor.<child>_current_lesson` | Subject, or `Registration`, `Break`, `Not in school`. Attributes: room, period, start, end |
| `sensor.<child>_next_lesson` | Next lesson's subject (looks ahead across days). Same attributes |
| `sensor.<child>_school_day_start` / `_school_day_end` | Today's first/last period, or the next school day's once today has finished |
| `sensor.<child>_next_school_day` | Next date after today with a timetable. Attribute `lessons` |
| `sensor.<child>_lessons_today` / `_lessons_tomorrow` | Number of lessons that day (0 at weekends and holidays). Attribute `lessons` |
| `sensor.<child>_timetable_week` | Week letter for schools on a multi-week cycle (A/B), for the current or next school day |
| `sensor.<child>_kit_today` / `_kit_for_next_school_day` | Number of kit reminders for today's lessons / the next school day's, from the kit rules in options. Attributes: `items` (for example `PE kit`), `date` |
| `binary_sensor.<child>_school_today` / `_school_tomorrow` | On if there is a timetable that day. Holidays are detected automatically |
| `calendar.<child>_timetable` | One event per lesson |

**Homework** (every 30 minutes, every 2 hours between 22:00 and 06:00; due today/tomorrow roll over at midnight)

| Entity | Description |
|---|---|
| `sensor.<child>_homework_outstanding` | Homework not marked complete and due within the next 7 days (set in options). Attribute `homework` lists them, each with `days_until_due` |
| `sensor.<child>_homework_urgency` | `today`, `tomorrow`, `later` or `none`, from the soonest outstanding homework. Handy for colouring cards |
| `sensor.<child>_homework_due_today` / `_due_tomorrow` | Outstanding homework due that day |
| `sensor.<child>_next_homework_due` | Earliest due date, with subject, task, status and description |
| `sensor.<child>_overdue_homework` | Disabled by default. Incomplete homework due in the last 14 days. Many schools do not track submission in Edulink, so this can be noisy |
| `todo.<child>_homework` | Current homework as a read-only to-do list |
| `calendar.<child>_homework` | All-day events on due dates |

**Catering** (every 15 minutes 07:30 to 16:00 on school days, otherwise every 2 hours. School holidays are taken from the timetable, so catering polls slowly during half-term)

| Entity | Description |
|---|---|
| `sensor.<child>_catering_balance` | Balance in GBP |
| `binary_sensor.<child>_low_catering_balance` | On below the threshold set in options (default £5) |
| `sensor.<child>_last_purchase` | Time of last purchase. Attributes: items, total |
| `sensor.<child>_spent_today` / `_spent_this_week` | Spend since midnight / Monday. Recorded as long-term statistics (resetting daily / weekly), so a statistics graph can chart spend per day or week. Spent today has attributes `purchases` and `items` (item names bought today) |
| `sensor.<child>_last_top_up` | Amount of the last top-up in GBP. Attribute `time` |

Edulink returns the catering balance in pounds, as a whole number when there are no pence and a decimal otherwise (for example `13.5` for £13.50).

## Options

- Low-balance threshold (default £5).
- Homework update interval (default 30 minutes).
- Outstanding homework look-ahead: how many days ahead the outstanding homework and urgency sensors look (default 7).
- Kit reminders: one `subject keyword = item` rule per line. A keyword matches whole words in a lesson's subject, ignoring case. The defaults are:
  ```
  Physical Education = PE kit
  PE = PE kit
  Food = Food Tech ingredients
  Art = Art materials
  ```
- Daytime catering update interval (default 15 minutes).

## Example automations

```yaml
# Homework reminder at 18:00 if anything is due tomorrow
automation:
  - alias: Homework due tomorrow
    triggers:
      - trigger: time
        at: "18:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.alex_homework_due_tomorrow
        above: 0
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Homework due tomorrow
          message: >
            {{ state_attr('sensor.alex_homework_due_tomorrow', 'homework')
               | map(attribute='subject') | join(', ') }}

  # Kit reminder the evening before (PE kit, ingredients and so on)
  - alias: Kit for tomorrow
    triggers:
      - trigger: time
        at: "19:30:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.alex_school_tomorrow
        state: "on"
      - condition: numeric_state
        entity_id: sensor.alex_kit_for_next_school_day
        above: 0
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            Pack for tomorrow:
            {{ state_attr('sensor.alex_kit_for_next_school_day', 'items') | join(', ') }}

  # Low dinner money
  - alias: Low catering balance
    triggers:
      - trigger: state
        entity_id: binary_sensor.alex_low_catering_balance
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Catering balance is £{{ states('sensor.alex_catering_balance') }}"
```

After any failed update the integration retries within 15 minutes. If one child's data fails to load, the other children are unaffected and the failing child keeps their last values.

## Privacy

The integration only reads timetable, homework and catering data. It does not read medical, personal, attendance, behaviour or document data.

Be aware of what ends up in Home Assistant's history. Sensor attributes include lesson subjects and rooms, homework subjects, tasks and descriptions, and catering items bought. Home Assistant's recorder keeps attributes along with states, so these are stored in your database and backups. Teacher names are deliberately kept out of sensor attributes; they appear only in calendar event descriptions, which Home Assistant excludes from the recorder. Exclude entities from the recorder if you do not want this history.

Diagnostics downloads contain counts only, never names, homework text or purchases.

## Development

```bash
pip install pytest-homeassistant-custom-component ruff
pytest
ruff check . && ruff format --check .
```

Tests use synthetic fixtures in `tests/fixtures/`. See `PLAN.md` for the observed API behaviour and `HANDOVER.md` for how to add further Edulink modules.
