"""
Renovation API router: includes per-area sub-routers under the shared prefix.

Phase 4 decomposition. ``api.py`` keeps importing ``router`` from here, and
the private helpers used by tests are re-exported for back-compat.
"""

from fastapi import APIRouter

from src.renovation.router_common import (  # noqa: F401
    _asset_scope_lookup,
    _dictionary_fingerprint,
    _import_cache_scope,
    _rewind_and_hash_upload,
)

router = APIRouter()

from src.renovation.router_dictionary import router as dictionary_router
from src.renovation.router_export import router as export_router
from src.renovation.router_import import router as import_router
from src.renovation.router_lifecycle import router as lifecycle_router
from src.renovation.router_plan import router as plan_router
from src.renovation.router_undo import router as undo_router

for sub in (import_router, plan_router, export_router, lifecycle_router, dictionary_router, undo_router):
    router.include_router(sub, prefix="/renovation", tags=["renovation"])
