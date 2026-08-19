"""Renovation seed data invariants (pure data checks, no DB)."""

from src.renovation.constants import R_ASSETS, RENOVATION_ROLE_NAMES
from src.renovation.models import RBlock, RProjectRole
from src.scripts.seed_database import RENOVATION_USERS


def test_r_assets_unique_codes() -> None:
    codes = [code for code, _, _ in R_ASSETS]
    assert len(codes) == len(set(codes))
    assert len(codes) == 17


def test_r_assets_have_nonempty_names() -> None:
    for code, repair_code, name in R_ASSETS:
        assert code
        assert repair_code.isupper()
        assert name


def test_renovation_role_names_cover_all_roles() -> None:
    assert set(RENOVATION_ROLE_NAMES) == set(RProjectRole)


def test_renovation_users_unique_emails() -> None:
    emails = [str(u["email"]) for u in RENOVATION_USERS]
    assert len(emails) == len(set(emails))


def test_renovation_users_roles_and_blocks() -> None:
    for user in RENOVATION_USERS:
        role = user["role"]
        assert role in RProjectRole
        block = user["block"]
        if role in (RProjectRole.BLOCK_OWNER, RProjectRole.ASSET_WORKER):
            assert block in RBlock, f"{user['email']} must carry a responsibility block"
        else:
            assert block is None, f"{user['email']} must not carry a block"


def test_renovation_worker_scope() -> None:
    for user in RENOVATION_USERS:
        if user["role"] != RProjectRole.ASSET_WORKER:
            continue
        assets = user["assets"]
        assert len(assets) == 1, f"{user['email']} must be scoped to exactly one asset"
        codes = {code for code, _, _ in R_ASSETS}
        assert assets[0] in codes
