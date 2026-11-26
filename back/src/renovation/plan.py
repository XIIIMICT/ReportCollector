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
from src.common.audit import UpdaterDisplay, configure_updater
from src.common.data_catalog import apply_table_comments


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


class RenovationReferenceValue(UpdaterDisplay, table=True):
    """A selectable option of a renovation reference catalog."""

    __tablename__ = "r_reference_values"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    catalog: RReferenceCatalog = Field(
        sa_column=Column(
            SAEnum(RReferenceCatalog, name="r_reference_catalog", values_callable=lambda e: [i.value for i in e]),
            nullable=False,
        )
    )
    label: str = Field(sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="true"))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")
    # Soft deletion is not used yet (deletes are hard); the column is the
    # forward-compatible marker: NULL means "not deleted".
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    __table_args__ = (
        # A catalog is a dictionary: one entry per label (case-insensitive).
        Index(
            "uq_r_reference_values_catalog_label",
            "catalog",
            text("lower(label)"),
            unique=True,
        ),
    )


class RenovationPlan(UpdaterDisplay, table=True):
    """A renovation plan version (working, pending approval, or archived)."""

    __tablename__ = "r_plans"
    __table_args__ = (
        Index("uq_r_plans_one_working", "project_id", unique=True, postgresql_where=text("state = 'working'")),
        Index(
            "uq_r_plans_one_pending",
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
    # Work-code generation of this plan version. Migration 0043 installs a
    # statement-level trigger on r_plan_rows that increments it transactionally
    # for every insert/delete and every work-code change, covering cell edits,
    # row lifecycle, undo/redo, imports and recompute alike. It is the plan-level
    # preflight cache token: no count/max over rows, and no app call site to forget.
    revision: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    created_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class RenovationPlanRow(UpdaterDisplay, table=True):
    """A single row of a renovation plan version (flat typed values)."""

    __tablename__ = "r_plan_rows"
    __table_args__ = (
        Index("uq_r_plan_rows_position", "plan_id", "position", unique=True),
        Index("ix_r_plan_rows_asset", "plan_id", "asset_id", "position"),
        # The header-filter facet lists always filter by `plan_id`, so these lead
        # with it. Migration 0025 rebuilt the five that used to be single-column
        # and added the text ones; the metadata has to match, because
        # `create_all` and the migration tests compare it with the real schema.
        Index("ix_r_plan_rows_plan_work_or_repair_code", "plan_id", "work_or_repair_code"),
        # Intended uniqueness contract (one code per plan version). Migration
        # 0042 authors this index but its rollout is BLOCKED until the live
        # duplicates are cleaned and the maintainer approves — see docs/TODO.md.
        # The metadata must already match the migration's head schema; apply
        # re-checks duplicates independently even before the index exists live.
        Index("uq_r_plan_rows_plan_work_or_repair_code", "plan_id", "work_or_repair_code", unique=True),
        Index("ix_r_plan_rows_plan_workshop_name", "plan_id", "workshop_name"),
        Index("ix_r_plan_rows_plan_department_or_installation_name", "plan_id", "department_or_installation_name"),
        Index("ix_r_plan_rows_plan_equipment_class", "plan_id", "equipment_class"),
        Index("ix_r_plan_rows_plan_equipment_category", "plan_id", "equipment_category"),
        Index("ix_r_plan_rows_plan_equipment_criticality_group_id", "plan_id", "equipment_criticality_group_id"),
        Index("ix_r_plan_rows_plan_work_category_id", "plan_id", "work_category_id"),
        Index("ix_r_plan_rows_plan_work_type_id", "plan_id", "work_type_id"),
        Index("ix_r_plan_rows_plan_cost_allocation_type_id", "plan_id", "cost_allocation_type_id"),
        Index("ix_r_plan_rows_plan_execution_method_id", "plan_id", "execution_method_id"),
        # «Дефектная ведомость/ВОР №». It is TEXT (0030) and every row carries its
        # own number, so the facet list has ~90k distinct values per plan: without
        # this index the facet query seq-scanned the whole plan (~1.4 s on 261k
        # rows, and it is asked TWICE per popover — the list and the column size).
        # An index-only scan answers both in ~60 ms.
        Index("ix_r_plan_rows_plan_defect_number", "plan_id", "defect_list_or_bill_of_quantities_number"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    plan_id: UUID = Field(foreign_key="r_plans.id", ondelete="CASCADE")
    # Nullable: rows may be added as empty drafts before their asset is chosen
    # (see add_plan_row); asset-less rows carry no renovation id and are removed
    # again on submit if still empty.
    asset_id: UUID | None = Field(None, foreign_key="r_assets.id", ondelete="RESTRICT")
    position: int = Field(sa_column=Column(BigInteger, nullable=False))
    baseline_generation: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    revision: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")
    work_or_repair_code: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    production_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    workshop_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    department_or_installation_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    equipment_class: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    equipment_category: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    equipment_unit_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    technical_location_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    process_position: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    inventory_number: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    functional_area: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    production_impact: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    equipment_criticality_group_id: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("r_reference_values.id"), nullable=True),
    )
    bpof_section: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    work_category_id: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("r_reference_values.id"), nullable=True),
    )
    work_type_id: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("r_reference_values.id"), nullable=True),
    )
    cost_allocation_type_id: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("r_reference_values.id"), nullable=True),
    )
    execution_method_id: UUID | None = Field(
        default=None,
        sa_column=Column(postgresql.UUID(as_uuid=True), ForeignKey("r_reference_values.id"), nullable=True),
    )
    work_executor_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    work_description: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    defect_list_or_bill_of_quantities_number: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    work_start_date: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    work_end_date: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    work_duration_days: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    labor_hours: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    work_cost_rub_ex_vat: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    customer_materials_cost_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    contractor_supplied_materials_cost_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    total_cost_rub_ex_vat: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    plan_after_proposed_adjustment_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    adj_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q1_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q1_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q1_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_january_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_january_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_january_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_february_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_february_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_february_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_march_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_march_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_march_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q2_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q2_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q2_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_april_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_april_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_april_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_may_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_may_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_may_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_june_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_june_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_june_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q3_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q3_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q3_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_july_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_july_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_july_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_august_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_august_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_august_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_september_total_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    adj_september_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_september_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q4_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q4_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_q4_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_october_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_october_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_october_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_november_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_november_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_november_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_december_total_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_december_cw_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adj_december_cm_cost_k_rub: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adjustment_work_volume_unit: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    adjustment_work_volume_quantity: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q1_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q1_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q1_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_january_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_january_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_january_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_february_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_february_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_february_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_march_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_march_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_march_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q2_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q2_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q2_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_april_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_april_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_april_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_may_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_may_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_may_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_june_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_june_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_june_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q3_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q3_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q3_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_july_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_july_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_july_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_august_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_august_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_august_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_september_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_september_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_september_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q4_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q4_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_q4_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_october_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_october_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_october_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_november_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_november_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_november_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_december_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_december_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_december_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    plan_adjustment_type: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    plan_adjustment_justification_document: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    original_plan_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q1_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q1_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q1_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_january_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_january_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_january_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_february_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_february_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_february_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_march_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_march_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_march_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q2_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q2_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q2_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_april_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_april_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_april_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_may_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_may_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_may_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_june_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_june_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_june_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q3_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q3_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q3_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_july_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_july_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_july_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_august_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_august_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_august_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_september_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_september_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_september_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q4_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q4_construction_works_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_q4_customer_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_october_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_october_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_october_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_november_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_november_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_november_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_december_total_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_december_cw_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    original_plan_december_cm_cost_k_rub: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    lotting_activity_direction_code: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    general_contractor_procurement_code: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    general_contractor_contract_date: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    general_contractor_contract_number: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    general_contractor_contract_price_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    general_contractor_counterparty_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    general_contractor_counterparty_prequalification_status: str | None = Field(
        default=None, sa_column=Column(Text(), nullable=True)
    )
    subcontractor_engagement_requirement: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    general_contractor_contract_issue_description: str | None = Field(
        default=None, sa_column=Column(Text(), nullable=True)
    )
    subcontract_contract_date: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    subcontract_contract_number: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    subcontractor_name: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    subcontractor_prequalification_status: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    has_materials_in_work_or_repair: bool | None = Field(default=None, sa_column=Column(Boolean(), nullable=True))
    required_materials_delivery_date: date | None = Field(default=None, sa_column=Column(Date(), nullable=True))
    total_materials_cost: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    centralized_supply_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    direct_supply_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    contractor_supply_materials_cost_thousand_rub_ex_vat: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    em_contracting_cs_pct: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    equipment_materials_contracting_direct_supply_readiness_percent: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    em_delivery_cs_pct: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 4), nullable=True))
    equipment_materials_delivery_direct_supply_readiness_percent: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    materials_are_critical: bool | None = Field(default=None, sa_column=Column(Boolean(), nullable=True))
    materials_readiness_issue_description: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    contractor_equipment_materials_supply_readiness_percent: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(18, 4), nullable=True)
    )
    contractor_supplied_materials_are_critical: bool | None = Field(
        default=None, sa_column=Column(Boolean(), nullable=True)
    )
    contractor_equipment_materials_supply_issue_description: str | None = Field(
        default=None, sa_column=Column(Text(), nullable=True)
    )
    note: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))
    # «Номер сметы»: the new first column of the «Дирекция КСиР» block. The
    # db name is reused from the retired «Корректировка» (dropped by 0007); its
    # column comment comes from field_header_paths() like every other col_*.
    estimate_number: str | None = Field(default=None, sa_column=Column(Text(), nullable=True))


for _model in (RenovationReferenceValue, RenovationPlan, RenovationPlanRow):
    apply_table_comments(SQLModel.metadata.tables[_model.__tablename__])
    if "updater_user_id" in _model.model_fields:
        configure_updater(_model)
