"""Transactional journal repository. Valkey is never authoritative for edits."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, literal
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.models import ACTION_SEQUENCE, CellState, EditStack, OutboxEvent
from src.collab.store import UNDO_LIMIT, get_collab_store, now_iso

HISTORY_TTL = timedelta(hours=24)


class JournalStore:
    """Journal operations in the caller's transaction; transport remains ephemeral."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(get_collab_store(), name)

    async def publish(self, room: str, message: dict) -> None:
        self.session.add(OutboxEvent(room=room, payload=message))

    async def _stack(self, user_id: str, session_id: str, scope: str) -> EditStack:
        now = datetime.now(UTC)
        await self.session.exec(
            insert(EditStack)
            .values(user_id=user_id, session_id=session_id, scope=scope, actions=[], expires_at=now + HISTORY_TTL)
            .on_conflict_do_nothing()
        )
        stack = (
            await self.session.exec(
                select(EditStack)
                .where(
                    col(EditStack.user_id) == user_id,
                    col(EditStack.session_id) == session_id,
                    col(EditStack.scope) == scope,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        if stack.expires_at <= now:
            stack.actions = []
        return stack

    async def next_action_id(self) -> int:
        return int((await self.session.exec(select(ACTION_SEQUENCE.next_value()))).one())

    async def push_actions(self, user_id: str, session_id: str, scope: str, actions: list[dict]) -> None:
        stack = await self._stack(user_id, session_id, scope)
        stack.actions = json.loads(json.dumps((actions + stack.actions)[:UNDO_LIMIT]))
        stack.expires_at = datetime.now(UTC) + HISTORY_TTL
        self.session.add(stack)
        await self.session.flush()

    async def list_actions(self, user_id: str, session_id: str, scope: str) -> list[dict]:
        stack = await self.session.get(EditStack, (user_id, session_id, scope), populate_existing=True)
        if stack is None or stack.expires_at <= datetime.now(UTC):
            return []
        return json.loads(json.dumps(stack.actions))

    async def next_action(
        self, user_id: str, session_id: str, scope: str, *, undone: bool, after_action_id: int | None = None
    ) -> dict | None:
        stack = await self._stack(user_id, session_id, scope)
        actions = reversed(stack.actions) if undone else iter(stack.actions)
        for action in actions:
            if after_action_id is not None and (
                (undone and action["id"] <= after_action_id) or (not undone and action["id"] >= after_action_id)
            ):
                continue
            if bool(action.get("undone")) == undone:
                return dict(action)
        return None

    async def undo_state(self, user_id: str, session_id: str, scope: str) -> tuple[int, int]:
        actions = await self.list_actions(user_id, session_id, scope)
        redo = sum(bool(action.get("undone")) for action in actions)
        return len(actions) - redo, redo

    async def mark_action_undone(self, user_id: str, session_id: str, scope: str, action_id: int, undone: bool) -> None:
        stack = await self._stack(user_id, session_id, scope)
        stack.actions = [
            {**action, "undone": undone} if action["id"] == action_id else action for action in stack.actions
        ]
        stack.expires_at = datetime.now(UTC) + HISTORY_TTL
        self.session.add(stack)
        await self.session.flush()

    async def clear_redo(self, user_id: str, session_id: str, scope: str) -> None:
        stack = await self._stack(user_id, session_id, scope)
        stack.actions = [action for action in stack.actions if not action.get("undone")]
        self.session.add(stack)
        await self.session.flush()

    async def clear_undo_stack(self, user_id: str, session_id: str, scope: str) -> None:
        await self.session.exec(
            delete(EditStack).where(
                col(EditStack.user_id) == user_id,
                col(EditStack.session_id) == session_id,
                col(EditStack.scope) == scope,
            )
        )

    async def clear_undo_state_for_scope(self, scope: str) -> None:
        await self.session.exec(delete(EditStack).where(col(EditStack.scope) == scope))

    async def clear_cell_states_for_table(self, table: str) -> None:
        await self.session.exec(delete(CellState).where(col(CellState.key).startswith(f"{table}/", autoescape=True)))

    async def bump_cell_state(self, cell_key: str, *, user_id: str, user_name: str, action_id: int) -> int:
        await self.session.exec(insert(CellState).values(key=cell_key, state={"revision": 0}).on_conflict_do_nothing())
        state = (
            await self.session.exec(
                select(CellState)
                .where(CellState.key == cell_key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        revision = int(state.state["revision"]) + 1
        state.state = {
            "revision": revision,
            "lastUserId": user_id,
            "lastUserName": user_name,
            "lastActionId": str(action_id),
            "updatedAt": now_iso(),
        }
        self.session.add(state)
        await self.session.flush()
        return revision

    async def get_cell_state(self, cell_key: str) -> dict | None:
        state = await self.session.get(CellState, cell_key, populate_existing=True)
        return dict(state.state) if state else None

    async def bump_cell_states(
        self, keys: list[str], *, user_id: str, user_name: str, action_id: int
    ) -> dict[str, int]:
        if not keys:
            return {}
        metadata = {
            "lastUserId": user_id,
            "lastUserName": user_name,
            "lastActionId": str(action_id),
            "updatedAt": now_iso(),
        }
        statement = insert(CellState).values(
            [{"key": key, "state": {**metadata, "revision": 1}} for key in sorted(set(keys))]
        )
        revision = cast(CellState.state["revision"].astext, Integer) + 1
        statement = statement.on_conflict_do_update(
            index_elements=["key"],
            set_={"state": statement.excluded.state.op("||")(func.jsonb_build_object(literal("revision"), revision))},
        ).returning(col(CellState.key), col(CellState.state))
        result = await self.session.exec(statement)
        return {key: state["revision"] for key, state in result}

    async def prune_expired(self) -> None:
        await self.session.exec(delete(EditStack).where(col(EditStack.expires_at) <= datetime.now(UTC)))
