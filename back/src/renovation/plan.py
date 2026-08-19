"""Renovation plan persistence models (generated from renovation-form.json, 0007)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlmodel import Field, SQLModel

from src.auth.models import utc_now


class RPlanState(StrEnum):
    """Lifecycle of a renovation plan version."""

    WORKING = "working"
    PENDING_APPROVAL = "pending_approval"
    ARCHIVED = "archived"


class RReferenceCatalog(StrEnum):
    """Fixed reference catalogs of the renovation plan."""

    CRITICALITY = "criticality"
    WORK_CATEGORY = "work_category"
    WORK_TYPE = "work_type"
    COST_ALLOCATION = "cost_allocation"
    EXECUTION_METHOD = "execution_method"


class RenovationReferenceValue(SQLModel, table=True):
    """A selectable option of a renovation reference catalog."""

    __tablename__ = "renovation_reference_values"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    catalog: RReferenceCatalog = Field(
        sa_column=Column(
            SAEnum(RReferenceCatalog, name="r_reference_catalog", values_callable=lambda e: [i.value for i in e]),
            nullable=False,
        )
    )
    # Stable key of the option within the catalog, e.g. В«work_type_001В».
    value_key: str = Field(sa_column=Column(Text, nullable=False))
    label: str = Field(sa_column=Column(Text, nullable=False))
    sort_order: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    payload: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="true"))
    __table_args__ = (Index("ix_renovation_reference_values_catalog_key", "catalog", "value_key", unique=True),)


class RenovationPlan(SQLModel, table=True):
    """A renovation plan version (working, pending approval, or archived)."""

    __tablename__ = "renovation_plans"
    __table_args__ = (
        Index("uq_renovation_plans_one_working", "project_id", unique=True, postgresql_where=text("state = 'working'")),
        Index(
            "uq_renovation_plans_one_pending",
            "project_id",
            unique=True,
            postgresql_where=text("state = 'pending_approval'"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", ondelete="CASCADE")
    state: RPlanState = Field(
        sa_column=Column(
            SAEnum(RPlanState, name="r_plan_state", values_callable=lambda e: [i.value for i in e]),
            nullable=False,
            server_default="working",
        )
    )
    baseline_generation: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    created_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class RenovationPlanRow(SQLModel, table=True):
    """A single row of a renovation plan version (flat typed values)."""

    __tablename__ = "renovation_plan_rows"
    __table_args__ = (
        Index("uq_renovation_plan_rows_position", "plan_id", "position", unique=True),
        Index("ix_renovation_plan_rows_asset", "plan_id", "asset_id", "position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    plan_id: UUID = Field(foreign_key="renovation_plans.id", ondelete="CASCADE")
    asset_id: UUID = Field(foreign_key="r_assets.id", ondelete="RESTRICT")
    position: int = Field(sa_column=Column(BigInteger, nullable=False))
    baseline_generation: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    revision: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    col_003: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_004: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_005: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_006: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_007: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_008: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_009: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_010: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_011: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_012: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_013: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_014: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_015: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("renovation_reference_values.id"), nullable=True),
    )
    col_016: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_017: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("renovation_reference_values.id"), nullable=True),
    )
    col_018: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("renovation_reference_values.id"), nullable=True),
    )
    col_019: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("renovation_reference_values.id"), nullable=True),
    )
    col_020: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("renovation_reference_values.id"), nullable=True),
    )
    col_021: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_022: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_023: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_024: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    col_025: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    col_026: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_027: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_028: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_029: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_030: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_031: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_032: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_033: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_034: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_035: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_036: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_037: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_038: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_039: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_040: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_041: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_042: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_043: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_044: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_045: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_046: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_047: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_048: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_049: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_050: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_051: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_052: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_053: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_054: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_055: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_056: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_057: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_058: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_059: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_060: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_061: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_062: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_063: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_064: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_065: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_066: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_067: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_068: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_069: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_070: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_071: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_072: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_073: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_074: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_075: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_076: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_077: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_078: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_079: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_080: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_081: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_082: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_083: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_084: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_085: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_086: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_087: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_088: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_089: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_090: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_091: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_092: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_093: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_094: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_095: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_096: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_097: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_098: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_099: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_100: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_101: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_102: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_103: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_104: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_105: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_106: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_107: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_108: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_109: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_110: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_111: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_112: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_113: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_114: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_115: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_116: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_117: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_118: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_119: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_120: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_121: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_122: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_123: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_124: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_125: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_126: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_127: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_128: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_129: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_130: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_131: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_132: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_133: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_134: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_135: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_136: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_137: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_138: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_139: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_140: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_141: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_142: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_143: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_144: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_145: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_146: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_147: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_148: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_149: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_150: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_151: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_152: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_153: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_154: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_155: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_156: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_157: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_158: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_159: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_160: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_161: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_162: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_163: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_164: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_165: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_166: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_167: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_168: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_169: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_170: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_171: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_172: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_173: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_174: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_175: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_176: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_177: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_178: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_179: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_180: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_181: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_182: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_183: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_184: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_185: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_186: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_187: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_188: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_189: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_190: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_191: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    col_192: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_193: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_194: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_195: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_196: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_197: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_198: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_199: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    col_200: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_201: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_202: bool | None = Field(default=None, sa_column=Column(Boolean(), nullable=True))
    col_203: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    col_204: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_205: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_206: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_207: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_208: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_209: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_210: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_211: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_212: bool | None = Field(default=None, sa_column=Column(Boolean(), nullable=True))
    col_213: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_214: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    col_215: bool | None = Field(default=None, sa_column=Column(Boolean(), nullable=True))
    col_216: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    col_217: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
