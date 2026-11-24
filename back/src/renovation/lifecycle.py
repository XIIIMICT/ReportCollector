"""
Renovation plan lifecycle: submit working -> pending, approve pending -> archived.

Moved verbatim from ``service.py`` (Phase 4): submits the working plan for
approval, approves it into an archive and opens the next working copy.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Text, and_, delete, func, insert, literal
from sqlalchemy import select as sa_select
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import lock_project
from src.collab.persistence import JournalStore
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import (
    editable_fields,
)
from src.renovation.models import RProjectRole
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RPlanState,
)
from src.renovation.plan_reads import (
    _plan_id_issues,
    _submit_id_validation_error,
)
from src.renovation.service_common import (
    _ERR_APPROVE_PERMISSION,
    _ERR_NO_PENDING_PLAN,
    _ERR_NO_WORKING_PLAN,
    _ERR_SUBMIT_PERMISSION,
)

from .errors import RenovationDictionaryError
from .plan_queries import _plan_by_state

if TYPE_CHECKING:
    from src.auth.models import User


async def submit_plan(session: AsyncSession, *, user: User, roles: list[RProjectRole]) -> RenovationPlan:
    """Send the working plan for approval (admin or a block owner)."""
    await lock_project(session, RENOVATION_PROJECT_KEY, exclusive=True)
    if RProjectRole.ADMIN not in roles and RProjectRole.BLOCK_OWNER not in roles:
        raise _ERR_SUBMIT_PERMISSION
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    now = datetime.now(UTC)
    # Empty drafts (rows added «in the moment») that still hold no data — even if
    # only their asset is picked — are dropped before the version is frozen.
    draft_cols = editable_fields()
    empty_columns = []
    for field in draft_cols:
        column = getattr(RenovationPlanRow, field)
        empty_columns.append((column.is_(None) | (column == "")) if isinstance(column.type, Text) else column.is_(None))
    await session.exec(delete(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id, and_(*empty_columns)))
    # The plan may be sent for approval only when every work ID is complete and
    # unique; otherwise the version would freeze with unusable identifiers.
    incorrect, duplicates, incorrect_rows, duplicate_rows = await _plan_id_issues(session, plan.id)
    if incorrect or duplicates:
        raise _submit_id_validation_error(incorrect, duplicates, incorrect_rows, duplicate_rows)
    plan.state = RPlanState.PENDING_APPROVAL
    plan.updated_at = now
    plan.updater_user_id = user.id
    session.add(plan)
    await session.flush()
    await session.refresh(plan)
    return plan


async def approve_plan(
    session: AsyncSession,
    *,
    user: User,
    roles: list[RProjectRole],
) -> tuple[RenovationPlan, RenovationPlan]:
    """
    Approve the pending plan: archive it and create a new working copy.

    The new working plan is a full copy of the approved rows with
    baseline_generation+1 (the approved version is frozen, the next cycle
    starts from the approved state).
    """
    if RProjectRole.ADMIN not in roles and RProjectRole.APPROVER not in roles:
        raise _ERR_APPROVE_PERMISSION
    await lock_project(session, RENOVATION_PROJECT_KEY, exclusive=True)
    plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_PENDING_PLAN
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    plan.state = RPlanState.ARCHIVED
    plan.updated_at = now
    plan.updater_user_id = user.id
    session.add(plan)

    working = RenovationPlan(
        project_id=plan.project_id,
        state=RPlanState.WORKING,
        baseline_generation=plan.baseline_generation + 1,
        created_at=now,
        created_by=updated_by,
        updated_at=now,
        updater_user_id=user.id,
    )
    session.add(working)
    await session.flush()
    # Copy inside PostgreSQL: no 94k-row ORM graph or millions of Python assignments.
    table = RenovationPlanRow.metadata.tables["r_plan_rows"]
    overrides = {
        "id": func.gen_random_uuid(),
        "plan_id": literal(working.id),
        "baseline_generation": literal(working.baseline_generation),
        "revision": literal(0),
        "created_at": literal(now),
        "updated_at": literal(now),
        "updater_user_id": literal(user.id),
    }
    columns = list(table.columns)
    await session.exec(
        insert(table).from_select(
            [column.name for column in columns],
            sa_select(*(overrides.get(column.name, column) for column in columns)).where(table.c.plan_id == plan.id),
            include_defaults=False,
        )
    )
    await JournalStore(session).clear_undo_state_for_scope("renovationPlan/")
    await session.flush()
    await session.refresh(plan)
    await session.refresh(working)
    return plan, working


# ─── Editable reference dictionaries ─────────────────────────────────────


# Dictionary error messages (module constants — project style).
_DICT_ASSETS_NOT_FOUND = RenovationDictionaryError("Один или несколько активов не найдены.")
