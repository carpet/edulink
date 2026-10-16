# Edulink One for Home Assistant

Unofficial Home Assistant integration for [Edulink One](https://www.edulinkone.com/), the UK school parent/learner portal. Provides homework, timetable and catering sensors, plus calendars and a homework to-do list, for each child on a parent account (or for a learner's own account).

Not affiliated with or endorsed by Edulink One or its developers. Edulink has no public API: this integration uses the same JSON-RPC calls as the Edulink website and may break if they change.

## Installation (HACS)

1. HACS, three-dot menu, **Custom repositories**, add `https://github.com/carpet/edulink` as type **Integration**.
2. Install **Edulink One** and restart Home Assistant.
3. **Settings, Devices & services, Add integration, Edulink One**.
4. Enter your school's postcode or Edulink school code, then your Edulink username and password. Parent accounts with several children can choose which to add.

Schools that only allow Microsoft sign-in are not supported.

Your Edulink username and password are stored in Home Assistant's config entry storage. They are needed because Edulink sessions expire after about two hours idle.

## Entities

One device per child. Entities are only created for modules your school has enabled.

**Timetable** (updated every 6 hours and after midnight; time-based states recalculated every minute)

| Entity | Description |
|---|---|
| `sensor.<child>_current_lesson` | Subject, or `Registration`, `Break`, `Not in school`. Attributes: room, teacher, period, start, end |
| `sensor.<child>_next_lesson` | Next lesson's subject (looks ahead across days). Same attributes |
| `sensor.<child>_school_day_start` / `_school_day_end` | Today's first/last period, or the next school day's once today has finished |
| `sensor.<child>_next_school_day` | Next date after today with a timetable. Attribute `lessons` |
| `sensor.<child>_lessons_tomorrow` | Number of lessons tomorrow (0 at weekends and holidays). Attribute `lessons` |
| `sensor.<child>_timetable_week` | Week letter for schools on a multi-week cycle (A/B), for the current or next school day |
| `binary_sensor.<child>_school_today` / `_school_tomorrow` | On if there is a timetable that day. Holidays are detected automatically |
| `calendar.<child>_timetable` | One event per lesson |

**Homework** (every 30 minutes, every 2 hours overnight)

| Entity | Description |
|---|---|
| `sensor.<child>_homework_outstanding` | Current homework not marked complete. Attribute `homework` lists them |
| `sensor.<child>_homework_due_today` / `_due_tomorrow` | Outstanding homework due that day |
| `sensor.<child>_next_homework_due` | Earliest due date, with subject, task, teacher and description |
| `sensor.<child>_overdue_homework` | Disabled by default. Incomplete homework due in the last 14 days. Many schools do not track submission in Edulink, so this can be noisy |
| `todo.<child>_homework` | Current homework as a read-only to-do list |
| `calendar.<child>_homework` | All-day events on due dates |

**Catering** (every 15 minutes 07:30 to 16:00 on weekdays, otherwise every 2 hours)

| Entity | Description |
|---|---|
| `sensor.<child>_catering_balance` | Balance in GBP |
| `binary_sensor.<child>_low_catering_balance` | On below the threshold set in options (default £5) |
| `sensor.<child>_last_purchase` | Time of last purchase. Attributes: items, total |
| `sensor.<child>_spent_today` / `_spent_this_week` | Spend since midnight / Monday |
| `sensor.<child>_last_top_up` | Time of last top-up. Attribute `amount` |

Edulink returns the catering balance as a whole number. It is correct for whole-pound balances; whether pence are dropped for other balances has not yet been confirmed.

## Options

Low-balance threshold, homework update interval and daytime catering update interval.

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

  # PE kit reminder the evening before
  - alias: PE kit
    triggers:
      - trigger: time
        at: "19:30:00"
    conditions:
      - condition: template
        value_template: >
          {{ state_attr('sensor.alex_lessons_tomorrow', 'lessons') | default([], true)
             | selectattr('subject', 'search', 'Physical Education') | list | count > 0 }}
    actions:
      - action: notify.mobile_app_phone
        data:
          message: PE tomorrow, pack the kit.

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

## Privacy

The integration only reads timetable, homework and catering data. It does not read medical, personal, attendance, behaviour or document data. Diagnostics downloads contain counts only, never names, homework text or purchases.

## Development

```bash
pip install pytest-homeassistant-custom-component ruff
pytest
ruff check . && ruff format --check .
```

Tests use synthetic fixtures in `tests/fixtures/`. See `PLAN.md` for the observed API behaviour and `HANDOVER.md` for how to add further Edulink modules.
