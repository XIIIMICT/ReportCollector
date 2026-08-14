"""Schemas for undo/redo responses and collaboration WebSocket events."""

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
    """Undo/redo body: the form (section + month) and the action the user chose to skip."""

    skipActionId: int | None = None
    section: str | None = None
    reportMonth: str | None = None


class UndoStateRead(BaseModel):
    """Undo/redo availability from the user's action stack."""

    canUndo: bool
    canRedo: bool


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
