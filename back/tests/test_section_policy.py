"""The per-section policy is the single source of truth for the workspace rules.

Front parity: `front/src/shared/grid/section-policy.ts` — the two files must
declare the same sections with the same modes.
"""

import re
from pathlib import Path

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
        "kppkUsers",
        "kppkRealizationPlan",
        "kppkRealizationFact",
        "kppkCost",
        "logisticsCountries",
        "logisticsTransportTypes",
        "logisticsOrgs",
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


def test_frontend_policy_matches_every_backend_mode() -> None:
    source = (Path(__file__).parents[2] / "front/src/shared/grid/section-policy.ts").read_text(encoding="utf-8")
    definitions: dict[str, dict[str, str | bool]] = {}

    def properties(body: str) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {}
        inherited = re.search(r"\.\.\.(\w+)", body)
        if inherited:
            result.update(definitions[inherited.group(1)])
        for key, quoted, boolean in re.findall(
            r'(collab|undo|export|rowOps|chunked)\s*:\s*(?:"([^"]+)"|(true|false))',
            body,
        ):
            result[key] = quoted or boolean == "true"
        return result

    for name, body in re.findall(r"const (\w+): SectionPolicy = \{(.*?)\};", source, re.DOTALL):
        definitions[name] = properties(body)
    section = source.split("const SECTION_POLICIES:", 1)[1].split("\n};", 1)[0]
    entries = dict(re.findall(r"^\s+(\w+): (\{[^\n]+\}|\w+),", section, re.MULTILINE))
    assert entries.keys() == SECTION_POLICIES.keys()
    for key, backend in SECTION_POLICIES.items():
        frontend = properties(entries[key]) if entries[key].startswith("{") else definitions[entries[key]]
        assert frontend == {
            "collab": backend.collab,
            "undo": backend.undo,
            "export": backend.export,
            "rowOps": backend.row_ops,
            "chunked": backend.chunked,
        }, key
