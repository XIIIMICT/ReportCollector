"""
Renovation plan persistence: reference values, plan versions, flat plan rows.

Revision ID: 0007_renovation_plans
Revises: 0006_renovation_roles
Create Date: 2026-08-19

- renovation_reference_values вЂ” the five fixed reference catalogs of the
  renovation plan (criticality, work category, work type, cost allocation,
  execution method), seeded with the options from renovation-form.json.
- renovation_plans вЂ” plan versions with an explicit lifecycle state
  (working / pending_approval / archived) and a baseline generation;
  at most one working and one pending version per project (partial unique).
- renovation_plan_rows вЂ” one typed, flat row per plan version: the 215
  application columns from renovation-form.json (col_003..col_217), plus
  the synthetic asset reference (col_002), monotonic position, baseline
  generation, revision, and audit fields. Calculated columns are stored
  and server-maintained; number columns keep NUMERIC(18, 4).

All operations are guarded the same way as 0006: fresh schemas are created
by create_all under the final names, existing schemas get only what is
missing; the reference-option seed is idempotent (ON CONFLICT DO NOTHING).
"""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_renovation_plans"
down_revision: str | None = "0006_renovation_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _type_exists(bind: sa.engine.Connection, name: str) -> bool:
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = current_schema() AND t.typname = :n
                """
            ),
            {"n": name},
        ).scalar()
        is not None
    )


def _table_exists(bind: sa.engine.Connection, name: str) -> bool:
    return name in set(sa.inspect(bind).get_table_names())


_REFERENCE_OPTIONS: list[tuple[str, str, str, int]] = [
    ("criticality", "criticality_001", "1", 1),
    ("criticality", "criticality_002", "2", 2),
    ("criticality", "criticality_003", "3", 3),
    ("criticality", "criticality_004", "4", 4),
    ("work_category", "work_category_001", "РўРћРёР ", 1),
    ("work_category", "work_category_002", "РћСЃС‚Р°РЅРѕРІРѕС‡РЅС‹Р№ РєР°РїРёС‚Р°Р»СЊРЅС‹Р№ СЂРµРјРѕРЅС‚", 2),
    ("work_category", "work_category_003", "Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹Рµ Р·Р°С‚СЂР°С‚С‹", 3),
    ("work_category", "work_category_004", "Р­РџР‘ Рё Р”Рћ", 4),
    ("work_category", "work_category_005", "Р—Р¤РёРћ", 5),
    ("work_category", "work_category_006", "РџРР ", 6),
    ("work_category", "work_category_007", "РџСЂРѕРµРєС‚ СѓР»СѓС‡С€РµРЅРёСЏ", 7),
    ("work_category", "work_category_008", "Р¦РµР»РµРІР°СЏ РїСЂРѕРіСЂР°РјРјР°", 8),
    ("work_type", "work_type_001", "РўРћ", 1),
    ("work_type", "work_type_002", "РўР ", 2),
    ("work_type", "work_type_003", "РЎР ", 3),
    ("work_type", "work_type_004", "РљР ", 4),
    ("work_type", "work_type_005", "Р§РёСЃС‚РєР°", 5),
    ("work_type", "work_type_006", "РСЃРїС‹С‚Р°РЅРёСЏ", 6),
    ("work_type", "work_type_007", "РџРѕРІРµСЂРєР°", 7),
    ("work_type", "work_type_008", "РљР°Р»РёР±СЂРѕРІРєР°", 8),
    ("work_type", "work_type_009", "РР·РјРµСЂРµРЅРёСЏ", 9),
    ("work_type", "work_type_010", "РљРѕСЂСЂРµРєС‚РёСЂСѓСЋС‰РёРµ СЂР°Р±РѕС‚С‹", 10),
    ("work_type", "work_type_011", "Р Р°Р±РѕС‚С‹ РїРѕ С„Р°РєС‚РёС‡РµСЃРєРѕРјСѓ С‚РµС….СЃРѕСЃС‚РѕСЏРЅРёСЋ", 11),
    ("work_type", "work_type_012", "РўРћ", 12),
    ("work_type", "work_type_013", "РўР ", 13),
    ("work_type", "work_type_014", "РЎР ", 14),
    ("work_type", "work_type_015", "РљР ", 15),
    ("work_type", "work_type_016", "Р§РёСЃС‚РєР°", 16),
    ("work_type", "work_type_017", "РСЃРїС‹С‚Р°РЅРёСЏ", 17),
    ("work_type", "work_type_018", "РџРѕРІРµСЂРєР°", 18),
    ("work_type", "work_type_019", "РљР°Р»РёР±СЂРѕРІРєР°", 19),
    ("work_type", "work_type_020", "РР·РјРµСЂРµРЅРёСЏ", 20),
    ("work_type", "work_type_021", "РљРѕСЂСЂРµРєС‚РёСЂСѓСЋС‰РёРµ СЂР°Р±РѕС‚С‹", 21),
    ("work_type", "work_type_022", "Р Р°Р±РѕС‚С‹ РїРѕ С„Р°РєС‚РёС‡РµСЃРєРѕРјСѓ С‚РµС….СЃРѕСЃС‚РѕСЏРЅРёСЋ", 22),
    (
        "work_type",
        "work_type_023",
        "Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹Рµ СЂР°Р±РѕС‚С‹ (Р»РµСЃР°, РёР·РѕР»СЏС†РёСЏ Рё РїСЂ)",
        23,
    ),
    ("work_type", "work_type_024", "РћСЃРЅР°С‰РµРЅРЅРѕСЃС‚СЊ СЂРµРјРѕРЅС‚РЅРѕРіРѕ РїРµСЂСЃРѕРЅР°Р»Р°", 24),
    (
        "work_type",
        "work_type_025",
        "РўРћРёР  РЅРµРїСЂРѕРёР·РІРѕРґСЃС‚РІРµРЅРЅС‹С… РѕР±СЉРµРєС‚РѕРІ РїСЂРµРґРїСЂРёСЏС‚РёСЏ",
        25,
    ),
    (
        "work_type",
        "work_type_026",
        "Р Р°СЃС…РѕРґС‹ Р±РµР· РІР»РёСЏРЅРёСЏ РЅР° РјРµС…Р°РЅРёС‡РµСЃРєСѓСЋ РіРѕС‚РѕРІРЅРѕСЃС‚СЊ",
        26,
    ),
    ("work_type", "work_type_027", "Р­РєСЃРїРµСЂС‚РёР·Р°", 27),
    ("work_type", "work_type_028", "РўРµС…. РѕСЃРІРёРґРµС‚РµР»СЊСЃС‚РІРѕРІР°РЅРёРµ", 28),
    ("work_type", "work_type_029", "Р”РёР°РіРЅРѕСЃС‚РёС‡РµСЃРєРёРµ РѕР±СЃР»РµРґРѕРІР°РЅРёСЏ", 29),
    ("work_type", "work_type_030", "РРЅСЃРїРµРєС†РёРё", 30),
    (
        "work_type",
        "work_type_031",
        "Р—Р°РјРµРЅР° С„РёР·РёС‡РµСЃРєРё РёР·РЅРѕС€РµРЅРЅРѕРіРѕ РѕР±РѕСЂСѓРґРѕРІР°РЅРёСЏ",
        31,
    ),
    ("work_type", "work_type_032", "Р РµРєРѕРЅСЃС‚СЂСѓРєС†РёСЏ", 32),
    ("work_type", "work_type_033", "РњРѕРґРµСЂРЅРёР·Р°С†РёСЏ", 33),
    ("work_type", "work_type_034", "РўРµС…РЅРёС‡РµСЃРєРѕРµ РїРµСЂРµРІРѕРѕСЂСѓР¶РµРЅРёРµ", 34),
    ("work_type", "work_type_035", "РљР°РїРёС‚Р°Р»СЊРЅРѕРµ СЃС‚СЂРѕРёС‚РµР»СЊСЃС‚РІРѕ", 35),
    ("cost_allocation", "cost_allocation_001", "CAPEX", 1),
    ("cost_allocation", "cost_allocation_002", "OPEX", 2),
    ("cost_allocation", "cost_allocation_003", "РЎС‡РµС‚ 91", 3),
    ("execution_method", "execution_method_001", "РЎРѕР±СЃС‚РІРµРЅРЅС‹Рµ СЃРёР»С‹", 1),
    ("execution_method", "execution_method_002", "РҐРёРјСЂРµРјРѕРЅС‚", 2),
    ("execution_method", "execution_method_003", "РќРҐР”", 3),
    ("execution_method", "execution_method_004", "РЈРЎРћ", 4),
]


def upgrade() -> None:
    bind = op.get_bind()

    if not _type_exists(bind, "r_plan_state"):
        op.execute(sa.text("CREATE TYPE r_plan_state AS ENUM ('working', 'pending_approval', 'archived')"))
    if not _type_exists(bind, "r_reference_catalog"):
        op.execute(
            sa.text(
                "CREATE TYPE r_reference_catalog AS ENUM ('criticality', 'work_category', 'work_type', 'cost_allocation', 'execution_method')"
            )
        )

    if not _table_exists(bind, "renovation_reference_values"):
        op.create_table(
            "renovation_reference_values",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "catalog",
                postgresql.ENUM(
                    "criticality",
                    "work_category",
                    "work_type",
                    "cost_allocation",
                    "execution_method",
                    name="r_reference_catalog",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("value_key", sa.Text(), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_renovation_reference_values_catalog_key",
            "renovation_reference_values",
            ["catalog", "value_key"],
            unique=True,
        )

    if not _table_exists(bind, "renovation_plans"):
        op.create_table(
            "renovation_plans",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "state",
                postgresql.ENUM("working", "pending_approval", "archived", name="r_plan_state", create_type=False),
                nullable=False,
                server_default="working",
            ),
            sa.Column("baseline_generation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.Text(), nullable=False, server_default=""),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_renovation_plans_one_working",
            "renovation_plans",
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("state = 'working'"),
        )
        op.create_index(
            "uq_renovation_plans_one_pending",
            "renovation_plans",
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("state = 'pending_approval'"),
        )

    if not _table_exists(bind, "renovation_plan_rows"):
        op.create_table(
            "renovation_plan_rows",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("position", sa.BigInteger(), nullable=False),
            sa.Column("baseline_generation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.Text(), nullable=False, server_default=""),
            sa.Column("col_003", sa.Text(), nullable=True),
            sa.Column("col_004", sa.Text(), nullable=True),
            sa.Column("col_005", sa.Text(), nullable=True),
            sa.Column("col_006", sa.Text(), nullable=True),
            sa.Column("col_007", sa.Text(), nullable=True),
            sa.Column("col_008", sa.Text(), nullable=True),
            sa.Column("col_009", sa.Text(), nullable=True),
            sa.Column("col_010", sa.Text(), nullable=True),
            sa.Column("col_011", sa.Text(), nullable=True),
            sa.Column("col_012", sa.Text(), nullable=True),
            sa.Column("col_013", sa.Text(), nullable=True),
            sa.Column("col_014", sa.Text(), nullable=True),
            sa.Column(
                "col_015", postgresql.UUID(as_uuid=True), sa.ForeignKey("renovation_reference_values.id"), nullable=True
            ),
            sa.Column("col_016", sa.Text(), nullable=True),
            sa.Column(
                "col_017", postgresql.UUID(as_uuid=True), sa.ForeignKey("renovation_reference_values.id"), nullable=True
            ),
            sa.Column(
                "col_018", postgresql.UUID(as_uuid=True), sa.ForeignKey("renovation_reference_values.id"), nullable=True
            ),
            sa.Column(
                "col_019", postgresql.UUID(as_uuid=True), sa.ForeignKey("renovation_reference_values.id"), nullable=True
            ),
            sa.Column(
                "col_020", postgresql.UUID(as_uuid=True), sa.ForeignKey("renovation_reference_values.id"), nullable=True
            ),
            sa.Column("col_021", sa.Text(), nullable=True),
            sa.Column("col_022", sa.Text(), nullable=True),
            sa.Column("col_023", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_024", sa.Date(), nullable=True),
            sa.Column("col_025", sa.Date(), nullable=True),
            sa.Column("col_026", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_027", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_028", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_029", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_030", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_031", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_032", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_033", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_034", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_035", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_036", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_037", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_038", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_039", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_040", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_041", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_042", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_043", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_044", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_045", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_046", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_047", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_048", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_049", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_050", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_051", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_052", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_053", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_054", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_055", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_056", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_057", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_058", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_059", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_060", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_061", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_062", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_063", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_064", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_065", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_066", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_067", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_068", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_069", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_070", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_071", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_072", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_073", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_074", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_075", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_076", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_077", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_078", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_079", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_080", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_081", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_082", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_083", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_084", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_085", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_086", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_087", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_088", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_089", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_090", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_091", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_092", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_093", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_094", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_095", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_096", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_097", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_098", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_099", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_100", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_101", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_102", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_103", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_104", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_105", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_106", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_107", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_108", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_109", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_110", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_111", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_112", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_113", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_114", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_115", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_116", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_117", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_118", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_119", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_120", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_121", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_122", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_123", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_124", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_125", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_126", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_127", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_128", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_129", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_130", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_131", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_132", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_133", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_134", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_135", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_136", sa.Text(), nullable=True),
            sa.Column("col_137", sa.Text(), nullable=True),
            sa.Column("col_138", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_139", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_140", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_141", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_142", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_143", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_144", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_145", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_146", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_147", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_148", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_149", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_150", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_151", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_152", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_153", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_154", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_155", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_156", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_157", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_158", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_159", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_160", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_161", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_162", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_163", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_164", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_165", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_166", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_167", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_168", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_169", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_170", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_171", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_172", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_173", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_174", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_175", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_176", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_177", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_178", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_179", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_180", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_181", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_182", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_183", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_184", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_185", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_186", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_187", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_188", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_189", sa.Text(), nullable=True),
            sa.Column("col_190", sa.Text(), nullable=True),
            sa.Column("col_191", sa.Date(), nullable=True),
            sa.Column("col_192", sa.Text(), nullable=True),
            sa.Column("col_193", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_194", sa.Text(), nullable=True),
            sa.Column("col_195", sa.Text(), nullable=True),
            sa.Column("col_196", sa.Text(), nullable=True),
            sa.Column("col_197", sa.Text(), nullable=True),
            sa.Column("col_198", sa.Text(), nullable=True),
            sa.Column("col_199", sa.Date(), nullable=True),
            sa.Column("col_200", sa.Text(), nullable=True),
            sa.Column("col_201", sa.Text(), nullable=True),
            sa.Column("col_202", sa.Boolean(), nullable=True),
            sa.Column("col_203", sa.Date(), nullable=True),
            sa.Column("col_204", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_205", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_206", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_207", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_208", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_209", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_210", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_211", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_212", sa.Boolean(), nullable=True),
            sa.Column("col_213", sa.Text(), nullable=True),
            sa.Column("col_214", sa.Numeric(18, 4), nullable=True),
            sa.Column("col_215", sa.Boolean(), nullable=True),
            sa.Column("col_216", sa.Text(), nullable=True),
            sa.Column("col_217", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["plan_id"], ["renovation_plans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["r_assets.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_renovation_plan_rows_position", "renovation_plan_rows", ["plan_id", "position"], unique=True
        )
        op.create_index("ix_renovation_plan_rows_asset", "renovation_plan_rows", ["plan_id", "asset_id", "position"])

    # Seed the five fixed catalogs from renovation-form.json (idempotent).
    bind.execute(
        sa.text(
            """
            INSERT INTO renovation_reference_values (id, catalog, value_key, label, sort_order)
            VALUES (:id, :catalog, :value_key, :label, :sort_order)
            ON CONFLICT (catalog, value_key) DO NOTHING
            """
        ),
        [
            {
                "id": uuid5(NAMESPACE_URL, f"{catalog}:{value_key}"),
                "catalog": catalog,
                "value_key": value_key,
                "label": label,
                "sort_order": sort_order,
            }
            for catalog, value_key, label, sort_order in _REFERENCE_OPTIONS
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "renovation_plan_rows"):
        op.drop_table("renovation_plan_rows")
    if _table_exists(bind, "renovation_plans"):
        op.drop_table("renovation_plans")
    if _table_exists(bind, "renovation_reference_values"):
        op.drop_table("renovation_reference_values")

    if _type_exists(bind, "r_plan_state"):
        op.execute(sa.text("DROP TYPE r_plan_state"))
    if _type_exists(bind, "r_reference_catalog"):
        op.execute(sa.text("DROP TYPE r_reference_catalog"))
