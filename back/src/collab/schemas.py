"""Схемы ответов undo/redo и WebSocket-событий совместного редактирования."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class UndoConflictRead(BaseModel):
    rowId: UUID
    field: str
    user: str = ""
    actionId: int
    reason: Literal["conflict", "locked"]


class UndoRequest(BaseModel):
    """Тело undo/redo: действие, которое пользователь решил пропустить."""

    skipActionId: int | None = None


class UndoResponse(BaseModel):
    applied: bool
    reason: Literal["applied", "empty", "conflict", "locked"] | None = None
    conflicts: list[UndoConflictRead] = []
    section: str | None = None
    reportMonth: date | None = None
    rowId: UUID | None = None
    field: str | None = None
    value: object | None = None
    revision: int | None = None
