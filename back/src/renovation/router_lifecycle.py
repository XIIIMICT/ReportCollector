"""
Submit / approve endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

from fastapi import APIRouter, HTTPException, status

from src.renovation.schemas import (
    RenovationApproveRead,
    RenovationPlanVersionRead,
)
from src.renovation.service import (
    RenovationPermissionError,
    RenovationPlanNotFoundError,
    RenovationPlanStateError,
    approve_plan,
    submit_plan,
)

router = APIRouter()

from src.renovation.router_common import (  # noqa: F401
    PLAN_EXPORT_RENDER_VERSION,
    PLAN_ROWS_JSON,
    RENOVATION_SECTION,
    AccessDependency,
    AdminDependency,
    RenovationAccess,
    SessionDependency,
    _asset_scope_lookup,
    _dictionary_fingerprint,
    _import_cache_scope,
    _publish_rows_changed,
    _renovation_project_id_or_error,
    _rewind_and_hash_upload,
    _stage_upload,
    _token,
    _upload_too_large,
    _version_read,
    require_renovation_access,
    require_renovation_admin,
)


@router.post("/plan/submit", response_model=RenovationPlanVersionRead)
async def post_renovation_submit(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationPlanVersionRead:
    """Send the working plan for approval (admin or a block owner)."""
    try:
        plan = await submit_plan(session, user=access.user, roles=access.roles)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _publish_rows_changed(session, access)
    return await _version_read(session, plan)


@router.post("/plan/approve", response_model=RenovationApproveRead)
async def post_renovation_approve(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationApproveRead:
    """Approve the pending plan (admin or approver): archive + new working copy."""
    try:
        archived, working = await approve_plan(session, user=access.user, roles=access.roles)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _publish_rows_changed(session, access)
    return RenovationApproveRead(
        archived=await _version_read(session, archived),
        working=await _version_read(session, working),
    )


# ─── Editable reference dictionaries (project admin only) ───────────────
