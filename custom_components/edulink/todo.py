"""Homework to-do list for Edulink One.

Ticking an item marks the homework submitted in Edulink, and unticking it
reverses that. This is the integration's only write to the school's system.
"""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import EdulinkError
from .const import DOMAIN
from .coordinator import EdulinkConfigEntry
from .entity import EdulinkEntity
from .models import HomeworkList

PARALLEL_UPDATES = 1

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
    """Current homework as a to-do list. Only the done status can be changed."""

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

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

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Mark a homework item submitted or not submitted in Edulink."""
        hw = next((hw for hw in self.learner_data.current if hw.id == item.uid), None)
        if hw is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="homework_not_found"
            )
        # Fields left out arrive as None. Anything else must match, because
        # Edulink's homework text and dates are not ours to edit.
        if (
            item.status is None
            or item.summary not in (None, f"{hw.subject}: {hw.activity}")
            or item.due not in (None, hw.due)
            or item.description not in (None, hw.description)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="homework_only_status"
            )
        completed = item.status == TodoItemStatus.COMPLETED
        if completed == hw.completed:
            return
        try:
            await self.coordinator.client.homework_completed(
                self.learner.id, hw.id, hw.source, completed
            )
        except EdulinkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="homework_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()
