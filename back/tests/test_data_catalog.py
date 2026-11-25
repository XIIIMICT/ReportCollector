"""Complete, normalized catalog descriptions and renovation header hierarchies."""

import json
from importlib import import_module

import pytest
from sqlalchemy import Column, MetaData, Table, Text
from sqlmodel import SQLModel

from src import models as _models  # noqa: F401
from src.collab import models as _collab_models  # noqa: F401
from src.common.data_catalog import TABLE_COMMENTS, column_comments_for
from src.renovation.form_schema import (
    FORM_DATA_PATH,
    _load_form,
    catalog_labels,
    field_header_paths,
    field_metas,
    header_rows,
    normalize_label,
)


def test_catalog_covers_every_application_table_and_column() -> None:
    # 0024 added `kppk_markets` (+6 columns) and swapped the text `product`/
    # `region` of the realization copies for `product_id`/`market_id` (same count).
    assert len(SQLModel.metadata.tables) == 35
    # 494: 0021 dropped `value_key`, 0022 swapped `payload` for `deleted_at`,
    # 0024 added the market dictionary's 6 columns.
    # 0026 adds the durable operation queue; 0028 adds its bounded result;
    # 0029 adds the job's worker lifecycle metadata (phase, worker, start, heartbeat).
    # 0030 drops `r_assets.code` (the internal slug; `repair_code` becomes `code`).
    # 0033 adds the «Класс опасности» dictionary (2 columns).
    # 0035 adds the «Ставки» main table (21 columns).
    # 0037 adds the asset grants of a «Работник на активе» (6 columns).
    # 0040 re-adds r_plan_rows.estimate_number as «Номер сметы» (1 column).
    assert sum(len(table.c) for table in SQLModel.metadata.tables.values()) == 554
    assert set(TABLE_COMMENTS) == set(SQLModel.metadata.tables)
    for table in SQLModel.metadata.tables.values():
        assert table.comment == TABLE_COMMENTS[table.name].rstrip(".")
        assert not table.comment.endswith(".")
        expected = column_comments_for(table)
        assert set(expected) == set(table.c.keys())
        for column in table.c:
            assert column.comment == expected[column.name]
            if column.name in {"id", "created_at", "updated_at"}:
                assert column.comment is None
                continue
            assert column.comment
            assert column.comment == normalize_label(column.comment)
            assert not column.comment.endswith(".")
            assert "идентификатор" not in column.comment.lower()


def test_frozen_migration_matches_current_catalog() -> None:
    migration = import_module("migrations.versions.0015_updater_user_catalog")
    expected = {
        table.name: (table.comment, {column.name: column.comment for column in table.c})
        for table in SQLModel.metadata.tables.values()
    }
    # 0019/0020 dropped `r_reference_values.sort_order` and `.value_key`: the
    # frozen snapshot still describes them, the live metadata does not.
    # 0024 replaced the text `product`/`region` of the realization copies with
    # `product_id`/`market_id`, so the snapshot keeps the text descriptions.
    # 0030 renamed the assets' `repair_code` to `code` (the old pair is gone) and
    # retyped `r_plan_rows.defect_list_or_bill_of_quantities_number` as text — the snapshot keeps the old wording.
    _replaced = {
        "sort_order",
        "value_key",
        "payload",
        "product",
        "region",
        "repair_code",
        "defect_list_or_bill_of_quantities_number",
    }
    migration_tables = {
        name: (
            description,
            {column: comment for column, comment in columns.items() if column not in _replaced},
        )
        for name, (description, columns) in migration.COMMENTS.items()
    }
    # The asset dictionary's `code` was described as an internal slug; 0030 made
    # it the repair code, so the live catalog's wording is the expected one.
    expected["r_assets"] = (
        expected["r_assets"][0],
        {**expected["r_assets"][1], "code": migration.COMMENTS["r_assets"][1]["code"]},
    )
    # 0017 added `kppk_org_products.has_excise` after 0015 froze the snapshot.
    for name, (description, columns) in migration_tables.items():
        columns.update(
            {column.name: column.comment for column in SQLModel.metadata.tables[name].c if column.name not in columns}
        )
    # 0024 added `kppk_markets` — a table the frozen snapshot cannot know about,
    # so it is described by the LIVE catalog only.
    for name, (description, columns) in expected.items():
        if name not in migration_tables:
            migration_tables[name] = (description, dict(columns))
    # 0041 renamed EVERY r_plan_rows column to the semantic names frozen in its RENAME and the frontend decoder,
    # rebuilt the table in form order, so the frozen 0015 snapshot (col_XXX)
    # cannot be compared with the live catalog for this one table. The owner
    # explicitly waived pre-0041 schema compatibility (2026-11-24).
    expected.pop("r_plan_rows", None)
    migration_tables.pop("r_plan_rows", None)
    assert expected == migration_tables
    assert migration.PREVIOUS_COMMENTS == import_module("migrations.versions.0014_data_catalog_comments").COMMENTS


def test_new_columns_cannot_silently_get_generic_comments() -> None:
    table = Table("rm_assets", MetaData(), Column("undocumented", Text))
    with pytest.raises(ValueError, match="Missing data-catalog descriptions: rm_assets"):
        column_comments_for(table)


def test_form_normalizes_labels_without_changing_values_or_layout() -> None:
    source = json.loads(FORM_DATA_PATH.read_text(encoding="utf-8"))
    normalized = _load_form()
    assert normalize_label(" \tСтоимость  всего,\r\n тыс.\u00a0руб.  ") == "Стоимость всего, тыс. руб."
    for key in source.keys() - {"headerCells", "fields", "catalogs"}:
        assert normalized[key] == source[key]
    for key, labels in (("headerCells", {"text"}), ("fields", {"label", "categories"}), ("catalogs", {"label"})):
        for before, after in zip(source[key], normalized[key], strict=True):
            assert {k: v for k, v in before.items() if k not in labels} == {
                k: v for k, v in after.items() if k not in labels
            }
    labels = [cell.text for _, cells in header_rows() for cell in cells]
    labels += [field.label for field in field_metas()]
    labels += list(catalog_labels().values())
    labels += [part for field in normalized["fields"] for part in field["categories"]]
    assert all(label == normalize_label(label) for label in labels)


def test_renovation_comments_keep_full_display_hierarchy() -> None:
    paths = field_header_paths()
    assert set(paths) == {field.db_name for field in field_metas()}
    assert len(paths) == 217
    assert paths["col_002"] == "Предприятие"
    assert paths["estimate_number"] == "2) Дирекция КСиР → Номер сметы"
    assert paths["work_or_repair_code"] == "Идентификационный код работы/ремонта"
    assert paths["equipment_criticality_group_id"] == "1) Техническая дирекция → Группа критичности оборудования"
    assert paths["adj_march_cm_cost_k_rub"] == (
        "2) Дирекция КСиР → План с учетом предлагаемой корректировки → "
        "Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → "
        "в т.ч. по месяцам → Март → в т.ч. → МТР Заказчика"
    )
    assert any(path.startswith("3) Дирекция МТО → ") for path in paths.values())
    assert paths["note"] == "Примечание"
    assert all(
        a != b for path in paths.values() for a, b in zip(path.split(" → "), path.split(" → ")[1:], strict=False)
    )
