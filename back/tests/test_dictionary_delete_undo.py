"""Undo/redo of dictionary record deletions in the «Сырьё» project.

A deleted asset/basis/raw item is journaled as an «__deleted» snapshot: undo
restores the row and its links, redo deletes it again through the same service
path (so the business rules still apply).
"""

from conftest import signed_identity_headers
from httpx2 import AsyncClient
from sqlmodel import select

from src.raw_materials.models import DeliveryBasis, RawMaterial, RawMaterialAsset, RawMaterialUserAsset

ADMIN_HEADERS = signed_identity_headers(email="admin@example.ru", name="Admin User")
SESSION = "dict-delete-session"


async def _create(client: AsyncClient, path: str, payload: dict) -> dict:
    response = await client.post(path, headers=ADMIN_HEADERS, params={"session_id": SESSION}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _undo(client: AsyncClient, section: str) -> dict:
    response = await client.post(
        "/api/raw-materials/undo",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={"section": section},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _redo(client: AsyncClient, section: str) -> dict:
    response = await client.post(
        "/api/raw-materials/redo",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
        json={"section": section},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_deleted_asset_is_restored_by_undo_and_removed_by_redo(client: AsyncClient, db_session) -> None:
    created = await _create(client, "/api/raw-materials/assets", {"name": "Ундо-актив"})
    asset_id = created["id"]

    response = await client.delete(
        f"/api/raw-materials/assets/{asset_id}", headers=ADMIN_HEADERS, params={"session_id": SESSION}
    )
    assert response.status_code == 204
    assert await db_session.get(RawMaterialAsset, asset_id) is None

    undone = await _undo(client, "assets")
    assert undone["applied"] is True

    restored = await db_session.get(RawMaterialAsset, asset_id)
    assert restored is not None
    assert restored.name == "Ундо-актив"

    redone = await _redo(client, "assets")
    assert redone["applied"] is True
    assert await db_session.get(RawMaterialAsset, asset_id) is None


async def test_deleted_basis_roundtrip(client: AsyncClient, db_session) -> None:
    created = await _create(client, "/api/raw-materials/delivery-bases", {"name": "Ундо-базис"})
    basis_id = created["id"]
    response = await client.delete(
        f"/api/raw-materials/delivery-bases/{basis_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
    )
    assert response.status_code == 204

    assert (await _undo(client, "deliveryBases"))["applied"] is True
    restored = await db_session.get(DeliveryBasis, basis_id)
    assert restored is not None
    assert restored.name == "Ундо-базис"


async def test_deleted_raw_material_restores_its_asset_links(client: AsyncClient, db_session) -> None:
    asset = (await db_session.exec(select(RawMaterialAsset))).first()
    created = await _create(
        client,
        "/api/raw-materials/raw-materials",
        {"name": "Ундо-сырьё", "assetIds": [str(asset.id)]},
    )
    raw_id = created["id"]
    response = await client.delete(
        f"/api/raw-materials/raw-materials/{raw_id}",
        headers=ADMIN_HEADERS,
        params={"session_id": SESSION},
    )
    assert response.status_code == 204

    assert (await _undo(client, "rawMaterials"))["applied"] is True
    restored = await db_session.get(RawMaterial, raw_id)
    assert restored is not None
    links = (await db_session.exec(select(RawMaterialAsset).where(RawMaterialAsset.id == asset.id))).all()
    assert links, "the asset itself survived"
    from src.raw_materials.models import AssetRawMaterial

    link = (await db_session.exec(select(AssetRawMaterial).where(AssetRawMaterial.raw_material_id == raw_id))).first()
    assert link is not None
    assert link.asset_id == asset.id


async def test_undo_delete_restores_asset_user_grants(client: AsyncClient, db_session) -> None:
    created = await _create(client, "/api/raw-materials/assets", {"name": "Ундо-гранты"})
    asset_id = created["id"]
    grant = (await db_session.exec(select(RawMaterialUserAsset))).first()
    if grant is None:
        return
    # Give the new asset a grant, then delete it and undo.
    db_session.add(RawMaterialUserAsset(user_id=grant.user_id, asset_id=asset_id))
    await db_session.commit()
    response = await client.delete(
        f"/api/raw-materials/assets/{asset_id}", headers=ADMIN_HEADERS, params={"session_id": SESSION}
    )
    assert response.status_code == 204

    assert (await _undo(client, "assets"))["applied"] is True
    restored_grant = await db_session.get(RawMaterialUserAsset, (grant.user_id, asset_id))
    assert restored_grant is not None
