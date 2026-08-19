from fastapi import APIRouter

from .auth.router import router as auth_router
from .collab.ws import router as collab_ws_router
from .raw_materials.router import router as raw_materials_router
from .renovation.router import router as renovation_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(raw_materials_router)
api_router.include_router(renovation_router)
api_router.include_router(collab_ws_router)
