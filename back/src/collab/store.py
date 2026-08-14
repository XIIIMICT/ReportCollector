"""
Хранилище состояния совместного редактирования.

Общий интерфейс для двух реализаций:
- ValkeyStore — redis-py клиент: аренда ячеек (lease с TTL), присутствие
  в комнатах (hash + expire) и pub/sub для рассылки событий между воркерами;
- MemoryStore — внутрипроцессная реализация для тестов и запуска без Valkey
  (в одном воркере поведение идентично; без межворкерной синхронизации).

Выбор делается по VALKEY_URL: пустая строка — MemoryStore.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache

import redis.asyncio as redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

from src.config import get_settings

LEASE_TTL_SECONDS = 20
PRESENCE_TTL_SECONDS = 25
UNDO_LIMIT = 500  # максимальный размер стека действий на пользователя
UNDO_SCAN_LIMIT = 200  # глубина поиска последнего действия пользователя


def lease_key(cell_key: str, *, project: str) -> str:
    """Ключ аренды ячейки с учётом проекта: rc:lease:{project}:{table}/{row}/{field}."""
    return f"rc:lease:{project}:{cell_key}"


def undo_stack_key(user_id: str, scope: str) -> str:
    """Ключ стека undo пользователя на одной форме (scope = section/reportMonth)."""
    return f"rc:undo:{user_id}:{scope}"


def cell_state_key(cell_key: str) -> str:
    return f"rc:cellstate:{cell_key}"


def action_seq_key() -> str:
    return "rc:actionid"


def room_presence_key(room: str) -> str:
    return f"rc:presence:{room}"


def room_channel(room: str) -> str:
    return f"rc:room:{room}"


def _encode(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _decode(raw: str | bytes) -> dict:
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def _as_str(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


# Lua: продлить/снять аренду, только если владелец не изменился.
_RENEW_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

# Удалить из стека undo действия, помеченные undone=true (новая правка
# пользователя делает его redo-ветку недействительной).
_CLEAR_REDO_SCRIPT = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
local keep = {}
for _, raw in ipairs(items) do
    local ok, decoded = pcall(cjson.decode, raw)
    if not (ok and decoded['undone']) then
        table.insert(keep, raw)
    end
end
redis.call('DEL', KEYS[1])
if #keep > 0 then
    redis.call('RPUSH', KEYS[1], unpack(keep))
end
return #keep
"""


class ValkeyStore:
    """
    Арена, присутствие и pub/sub поверх Valkey/Redis.

    Ключи аренд принимаются уже «скоупленными» вызывающим кодом
    (lease_key(cell_key, project=...)): хранилище не знает про проекты.
    """

    def __init__(self, url: str) -> None:
        self._redis = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
            health_check_interval=30,
            retry=Retry(ExponentialBackoff(base=0.2, cap=1.5), retries=3),
            retry_on_timeout=True,
        )

    async def close(self) -> None:
        await self._redis.aclose()

    # ─── Аренда ячеек ────────────────────────────────────────────────────────

    async def acquire_lease(self, key: str, owner: dict, *, ttl: int = LEASE_TTL_SECONDS) -> dict | None:
        """Захватывает ячейку. None — успех; иначе — текущий владелец."""
        ok = await self._redis.set(key, _encode(owner), nx=True, ex=ttl)
        if ok:
            return None
        raw = await self._redis.get(key)
        return _decode(raw) if raw else None

    async def renew_lease(self, key: str, owner: dict, *, ttl: int = LEASE_TTL_SECONDS) -> bool:
        """Продлевает аренду, если владелец прежний."""
        return bool(await self._redis.eval(_RENEW_LEASE_SCRIPT, 1, key, _encode(owner), ttl))

    async def release_lease(self, key: str, owner: dict) -> bool:
        """Снимает аренду, если владелец прежний."""
        return bool(await self._redis.eval(_RELEASE_LEASE_SCRIPT, 1, key, _encode(owner)))

    async def get_lease(self, key: str) -> dict | None:
        raw = await self._redis.get(key)
        return _decode(raw) if raw else None

    async def any_active_lease(self, keys: list[str]) -> dict | None:
        """Владелец первой занятой ячейки среди переданных (None — все свободны)."""
        if not keys:
            return None
        values = await self._redis.mget(keys)
        for raw in values:
            if raw:
                return _decode(raw)
        return None

    # ─── Стек undo/redo пользователя ─────────────────────────────────────────

    async def next_action_id(self) -> int:
        """Глобальный сквозной идентификатор действия (для cellstate и ответов)."""
        return int(await self._redis.execute_command("INCR", action_seq_key()))

    async def push_actions(self, user_id: str, scope: str, actions: list[dict]) -> None:
        """Добавляет действия в начало стека формы (первые элементы — самые свежие)."""
        key = undo_stack_key(user_id, scope)
        pipe = self._redis.pipeline(transaction=True)
        pipe.lpush(key, *[_encode(action) for action in actions])
        pipe.ltrim(key, 0, UNDO_LIMIT - 1)
        await pipe.execute()

    async def next_action(
        self,
        user_id: str,
        scope: str,
        *,
        undone: bool,
        after_action_id: int | None = None,
    ) -> dict | None:
        """Ближайшее действие пользователя с нужным состоянием (свежие первыми)."""
        items = await self._redis.lrange(undo_stack_key(user_id, scope), 0, UNDO_SCAN_LIMIT - 1)
        for action in map(_decode, items):
            if after_action_id is not None and int(action["id"]) >= after_action_id:
                continue
            if bool(action.get("undone")) == undone:
                return action
        return None

    async def undo_state(self, user_id: str, scope: str) -> tuple[int, int]:
        """(число действий к отмене, число действий к возврату) на форме scope."""
        items = await self._redis.lrange(undo_stack_key(user_id, scope), 0, -1)
        can_undo = can_redo = 0
        for action in map(_decode, items):
            if bool(action.get("undone")):
                can_redo += 1
            else:
                can_undo += 1
        return can_undo, can_redo

    async def list_actions(self, user_id: str, scope: str) -> list[dict]:
        """Все действия пользователя на форме scope (свежие первыми) — для тестов."""
        items = await self._redis.lrange(undo_stack_key(user_id, scope), 0, -1)
        return [_decode(item) for item in items]

    async def mark_action_undone(self, user_id: str, scope: str, action_id: int, undone: bool) -> None:
        """Переключает состояние действия (undone=True — отменено, False — возвращено)."""
        key = undo_stack_key(user_id, scope)
        items = await self._redis.lrange(key, 0, -1)
        for index, raw in enumerate(items):
            action = _decode(raw)
            if action["id"] != action_id:
                continue
            action["undone"] = undone
            await self._redis.lset(key, index, _encode(action))
            return

    async def clear_redo(self, user_id: str, scope: str) -> None:
        """Удаляет отменённые действия формы: новая правка инвалидирует redo-ветку."""
        await self._redis.eval(_CLEAR_REDO_SCRIPT, 1, undo_stack_key(user_id, scope))

    async def clear_undo_stack(self, user_id: str, scope: str) -> None:
        """Полностью очищает стек формы пользователя."""
        await self._redis.delete(undo_stack_key(user_id, scope))

    async def clear_all_undo_state(self) -> None:
        """Очищает стеки всех пользователей и состояния ячеек (фиксация месяца)."""
        async for key in self._redis.scan_iter(match="rc:undo:*"):
            await self._redis.delete(key)
        async for key in self._redis.scan_iter(match="rc:cellstate:*"):
            await self._redis.delete(key)

    # ─── Состояние ячеек (ревизии, конфликт-детекция undo) ───────────────────

    async def bump_cell_state(self, cell_key: str, *, user_id: str, user_name: str, action_id: int) -> int:
        """Атомарно увеличивает ревизию ячейки и возвращает новое значение."""
        key = cell_state_key(cell_key)
        pipe = self._redis.pipeline(transaction=True)
        pipe.hincrby(key, "revision", 1)
        pipe.hset(
            key,
            mapping={
                "lastUserId": user_id,
                "lastUserName": user_name,
                "lastActionId": str(action_id),
                "updatedAt": now_iso(),
            },
        )
        results = await pipe.execute()
        return int(results[0])

    async def get_cell_state(self, cell_key: str) -> dict | None:
        raw = await self._redis.hgetall(cell_state_key(cell_key))
        return raw or None

    # ─── Присутствие в комнате ───────────────────────────────────────────────

    async def room_join(self, room: str, user_id: str, payload: dict) -> None:
        await self._redis.hset(room_presence_key(room), user_id, _encode(payload))
        await self._redis.expire(room_presence_key(room), PRESENCE_TTL_SECONDS)

    async def room_leave(self, room: str, user_id: str) -> None:
        await self._redis.hdel(room_presence_key(room), user_id)

    async def refresh_presence(self, room: str, user_id: str) -> None:
        """Продлевает присутствие активного участника (TTL сбрасывается)."""
        key = room_presence_key(room)
        if await self._redis.hget(key, user_id) is None:
            return
        await self._redis.expire(key, PRESENCE_TTL_SECONDS)

    async def room_members(self, room: str) -> dict[str, dict]:
        raw = await self._redis.hgetall(room_presence_key(room))
        return {_as_str(user_id): _decode(value) for user_id, value in raw.items()}

    # ─── Рассылка событий ────────────────────────────────────────────────────

    async def publish(self, room: str, message: dict) -> None:
        await self._redis.publish(room_channel(room), _encode(message))

    async def subscribe(self, room: str) -> AsyncIterator[dict]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(room_channel(room))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                yield _decode(message["data"])
        finally:
            await pubsub.unsubscribe(room_channel(room))
            await pubsub.aclose()


class MemoryStore:
    """Внутрипроцессная реализация (тесты, запуск без Valkey)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._leases: dict[str, tuple[dict, float]] = {}
        self._presence: dict[str, dict[str, tuple[dict, float]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._undo: dict[tuple[str, str], list[dict]] = {}
        self._cell_states: dict[str, dict] = {}
        self._action_seq = 0

    async def close(self) -> None:
        return None

    async def acquire_lease(self, key: str, owner: dict, *, ttl: int = LEASE_TTL_SECONDS) -> dict | None:
        async with self._lock:
            expires_at = time.monotonic() + ttl
            current = self._leases.get(key)
            if current is not None and current[1] > time.monotonic():
                return current[0]
            self._leases[key] = (owner, expires_at)
            return None

    async def renew_lease(self, key: str, owner: dict, *, ttl: int = LEASE_TTL_SECONDS) -> bool:
        async with self._lock:
            current = self._leases.get(key)
            if current is None or current[0] != owner or current[1] <= time.monotonic():
                return False
            self._leases[key] = (owner, time.monotonic() + ttl)
            return True

    async def release_lease(self, key: str, owner: dict) -> bool:
        async with self._lock:
            current = self._leases.get(key)
            if current is None or current[0] != owner:
                return False
            del self._leases[key]
            return True

    async def get_lease(self, key: str) -> dict | None:
        async with self._lock:
            current = self._leases.get(key)
            if current is None or current[1] <= time.monotonic():
                return None
            return current[0]

    async def any_active_lease(self, keys: list[str]) -> dict | None:
        """Владелец первой занятой ячейки среди переданных (None — все свободны)."""
        async with self._lock:
            for key in keys:
                current = self._leases.get(key)
                if current is not None and current[1] > time.monotonic():
                    return current[0]
        return None

    # ─── Стек undo/redo пользователя ─────────────────────────────────────────

    async def next_action_id(self) -> int:
        async with self._lock:
            self._action_seq += 1
            return self._action_seq

    async def push_actions(self, user_id: str, scope: str, actions: list[dict]) -> None:
        async with self._lock:
            stack = self._undo.setdefault((user_id, scope), [])
            for action in reversed(actions):
                stack.insert(0, action)
            del stack[UNDO_LIMIT:]

    async def next_action(
        self,
        user_id: str,
        scope: str,
        *,
        undone: bool,
        after_action_id: int | None = None,
    ) -> dict | None:
        async with self._lock:
            for action in self._undo.get((user_id, scope), [])[:UNDO_SCAN_LIMIT]:
                if after_action_id is not None and int(action["id"]) >= after_action_id:
                    continue
                if bool(action.get("undone")) == undone:
                    return action
        return None

    async def undo_state(self, user_id: str, scope: str) -> tuple[int, int]:
        async with self._lock:
            can_undo = can_redo = 0
            for action in self._undo.get((user_id, scope), []):
                if bool(action.get("undone")):
                    can_redo += 1
                else:
                    can_undo += 1
            return can_undo, can_redo

    async def list_actions(self, user_id: str, scope: str) -> list[dict]:
        async with self._lock:
            return [dict(action) for action in self._undo.get((user_id, scope), [])]

    async def mark_action_undone(self, user_id: str, scope: str, action_id: int, undone: bool) -> None:
        async with self._lock:
            for action in self._undo.get((user_id, scope), []):
                if action["id"] == action_id:
                    action["undone"] = undone
                    return

    async def clear_redo(self, user_id: str, scope: str) -> None:
        async with self._lock:
            stack = self._undo.get((user_id, scope))
            if stack is None:
                return
            self._undo[(user_id, scope)] = [action for action in stack if not action.get("undone")]

    async def clear_undo_stack(self, user_id: str, scope: str) -> None:
        async with self._lock:
            self._undo.pop((user_id, scope), None)

    async def clear_all_undo_state(self) -> None:
        async with self._lock:
            self._undo.clear()
            self._cell_states.clear()

    # ─── Состояние ячеек (ревизии, конфликт-детекция undo) ───────────────────

    async def bump_cell_state(self, cell_key: str, *, user_id: str, user_name: str, action_id: int) -> int:
        async with self._lock:
            state = self._cell_states.setdefault(cell_key, {"revision": 0})
            state["revision"] = int(state["revision"]) + 1
            state["lastUserId"] = user_id
            state["lastUserName"] = user_name
            state["lastActionId"] = str(action_id)
            state["updatedAt"] = now_iso()
            return state["revision"]

    async def get_cell_state(self, cell_key: str) -> dict | None:
        async with self._lock:
            state = self._cell_states.get(cell_key)
            return dict(state) if state is not None else None

    async def room_join(self, room: str, user_id: str, payload: dict) -> None:
        async with self._lock:
            room_members = self._presence.setdefault(room, {})
            room_members[user_id] = (payload, time.monotonic() + PRESENCE_TTL_SECONDS)

    async def room_leave(self, room: str, user_id: str) -> None:
        async with self._lock:
            self._presence.get(room, {}).pop(user_id, None)

    async def presence_expires_at(self, room: str, user_id: str) -> float | None:
        """Момент истечения присутствия участника (для тестов heartbeat)."""
        async with self._lock:
            current = self._presence.get(room, {}).get(user_id)
            return current[1] if current is not None else None

    async def refresh_presence(self, room: str, user_id: str) -> None:
        async with self._lock:
            room_members = self._presence.get(room, {})
            current = room_members.get(user_id)
            if current is not None and current[1] > time.monotonic():
                room_members[user_id] = (current[0], time.monotonic() + PRESENCE_TTL_SECONDS)

    async def room_members(self, room: str) -> dict[str, dict]:
        async with self._lock:
            now = time.monotonic()
            room_members = self._presence.get(room, {})
            return {user_id: payload for user_id, (payload, expires_at) in room_members.items() if expires_at > now}

    async def publish(self, room: str, message: dict) -> None:
        async with self._lock:
            for subscriber in self._subscribers.get(room, set()):
                if subscriber.full():
                    continue
                subscriber.put_nowait(message)

    async def subscribe(self, room: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.setdefault(room, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.get(room, set()).discard(queue)


type CollabStore = ValkeyStore | MemoryStore


@lru_cache(maxsize=1)
def get_collab_store() -> CollabStore:
    """Ленивый синглтон: Valkey по VALKEY_URL, иначе in-memory."""
    url = get_settings().valkey_url.strip()
    return ValkeyStore(url) if url else MemoryStore()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
