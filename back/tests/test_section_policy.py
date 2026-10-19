"""The per-section policy is the single source of truth for the workspace rules.

Front parity: `front/src/shared/grid/section-policy.ts` — the two files must
declare the same sections with the same modes.
"""

from src.common.section_policy import (
    SECTION_POLICIES,
    admin_room_section,
    is_collab_section,
    section_policy,
    undo_mode,
)


def test_every_project_section_declares_a_policy() -> None:
    expected = {
        "materialMovement",
        "rawMaterialPrices",
        "rawMaterials",
        "assets",
        "deliveryBases",
        "users",
        "renovationPlan",
        "renovationAssets",
        "renovationUsers",
        "kppkPositions",
        "kppkRaws",
        "kppkOrgs",
        "kppkRealizationPlan",
        "kppkRealizationFact",
    }
    assert set(SECTION_POLICIES) == expected


def test_dynamic_renovation_catalog_falls_back_to_the_dictionary_policy() -> None:
    policy = section_policy("renovationCatalog:material")
    assert policy.collab == "admin"
    assert policy.export == "member"


def test_forms_collaborate_for_every_member_and_dictionaries_only_for_admins() -> None:
    assert is_collab_section("materialMovement")
    assert not admin_room_section("materialMovement")
    assert is_collab_section("kppkOrgs")
    assert admin_room_section("kppkOrgs")


def test_only_the_chunked_plan_disables_select_all() -> None:
    chunked = {section for section, policy in SECTION_POLICIES.items() if policy.chunked}
    assert chunked == {"renovationPlan"}


def test_dictionaries_are_exportable_by_every_member() -> None:
    for section in ("rawMaterials", "assets", "deliveryBases", "users", "kppkOrgs", "renovationAssets"):
        assert section_policy(section).export == "member", section


def test_undo_mirrors_the_edit_rights() -> None:
    # Member-editable forms: every member undoes their own edits.
    assert undo_mode("materialMovement") == "member"
    # Admin-edited dictionaries: only an admin can undo there.
    assert undo_mode("rawMaterials") == "admin"
    assert undo_mode("kppkRaws") == "admin"
