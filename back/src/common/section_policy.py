"""
Per-section rules shared by the API, the collaboration rooms and the journal.

This is the backend half of the single source of truth; the front half is
`front/src/shared/grid/section-policy.ts`. Both must declare the same section
ids and agree on the modes — `back/tests/test_section_policy.py` checks parity.
The durable rules are recorded in `docs/COLLAB.md` and `docs/GRID.md`.

Why a module and not scattered constants: the "is this an admin-only room"
decision used to live in `collab/access.py` while the undo gate lived in
`collab/undo.py`, and the front repeated both with `MAIN_SECTIONS` checks. A
rule stated once here is what makes "editing works the same everywhere" true.
"""

from dataclasses import dataclass
from typing import Literal

CollabMode = Literal["full", "admin", "none"]
UndoMode = Literal["member", "admin", "none"]
ExportMode = Literal["member", "admin", "none"]
RowOpsMode = Literal["placeholder", "dialog", "plan", "none"]


@dataclass(frozen=True)
class SectionPolicy:
    """The rules of one form or dictionary."""

    collab: CollabMode
    undo: UndoMode
    export: ExportMode
    row_ops: RowOpsMode
    chunked: bool = False


_FORM = SectionPolicy(collab="full", undo="member", export="member", row_ops="none")
_DICTIONARY = SectionPolicy(collab="admin", undo="admin", export="member", row_ops="placeholder")
_PLAN = SectionPolicy(collab="full", undo="member", export="member", row_ops="plan", chunked=True)
# A table owned by another system: read-only, no room, no journal, no export.
_READ_ONLY = SectionPolicy(collab="none", undo="none", export="none", row_ops="none")

SECTION_POLICIES: dict[str, SectionPolicy] = {
    # «Сырьё»
    "materialMovement": _FORM,
    "rawMaterialPrices": _FORM,
    "rawMaterials": _DICTIONARY,
    "assets": _DICTIONARY,
    "deliveryBases": _DICTIONARY,
    "users": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="dialog"),
    # «Ремонт»
    "renovationPlan": _PLAN,
    "renovationAssets": _DICTIONARY,
    "renovationUsers": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="dialog"),
    # «КППК»
    "kppkPositions": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="placeholder"),
    "kppkRaws": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="placeholder"),
    "kppkOrgs": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="placeholder"),
    "kppkUsers": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="dialog"),
    "kppkCost": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="none"),
    # «Реализация»: the rows come from the source copies (no placeholders),
    # the editing and undo stay admin-only like every «КППК» surface.
    "kppkRealizationPlan": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="none"),
    "kppkRealizationFact": SectionPolicy(collab="admin", undo="admin", export="member", row_ops="none"),
    # «Логистика»: read straight from the external `dict` schema — nothing is
    # authored, journaled, exported or shared there.
    "logisticsCountries": _READ_ONLY,
    "logisticsTransportTypes": _READ_ONLY,
    "logisticsOrgs": _READ_ONLY,
}

#: The renovation catalogs are dynamic ids (`renovationCatalog:<catalog>`).
CATALOG_POLICY = _DICTIONARY


def section_policy(section: str) -> SectionPolicy:
    """Return the policy of a section; unknown renovation catalogs get the catalog one."""
    return SECTION_POLICIES.get(section, CATALOG_POLICY)


def admin_room_section(section: str) -> bool:
    """Whether only project admins may join this section's collaboration room."""
    return section_policy(section).collab == "admin"


def is_collab_section(section: str) -> bool:
    """Whether the section has a collaboration room at all."""
    return section_policy(section).collab != "none"


def undo_mode(section: str) -> UndoMode:
    """Who may undo/redo on this section."""
    return section_policy(section).undo
