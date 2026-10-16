"""Read-only homework to-do list for Edulink One.

Write-back (EduLink.HomeworkCompleted) is deliberately not implemented: the
call is untested and changes data on the school's system.
"""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EdulinkConfigEntry
from .entity import EdulinkEntity
from .models import HomeworkList

PARALLEL_UPDATES = 0

HOMEWORK_TODO = EntityDescription(key="homework_todo", translation_key="homework")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdulinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the homework to-do list."""
    runtime = entry.runtime_data
    if runtime.homework is None:
        return
    async_add_entities(
        HomeworkTodo(runtime.homework, learner, HOMEWORK_TODO)
        for learner in runtime.learners
    )


class HomeworkTodo(EdulinkEntity[HomeworkList], TodoListEntity):
    """Current homework as a read-only to-do list."""

    @property
    def todo_items(self) -> list[TodoItem]:
        """Current homework, soonest due first."""
        return [
            TodoItem(
                uid=hw.id,
                summary=f"{hw.subject}: {hw.activity}",
                status=(
                    TodoItemStatus.COMPLETED
                    if hw.completed
                    else TodoItemStatus.NEEDS_ACTION
                ),
                due=hw.due,
                description=hw.description,
            )
            for hw in sorted(self.learner_data.current, key=lambda hw: hw.due)
        ]
