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
    assert len(SQLModel.metadata.tables) == 30
    assert sum(len(table.c) for table in SQLModel.metadata.tables.values()) == 495
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
    assert expected == migration.COMMENTS
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
    assert len(paths) == 216
    assert paths["col_002"] == "Предприятие"
    assert paths["col_003"] == "Идентификационный код работы/ремонта"
    assert paths["col_015"] == "1) Техническая дирекция → Группа критичности оборудования"
    assert paths["col_046"] == (
        "2) Дирекция КСиР → План с учетом предлагаемой корректировки → "
        "Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → "
        "в т.ч. по месяцам → Март → в т.ч. → МТР Заказчика"
    )
    assert any(path.startswith("3) Дирекция МТО → ") for path in paths.values())
    assert paths["col_217"] == "Примечание"
    assert all(
        a != b for path in paths.values() for a, b in zip(path.split(" → "), path.split(" → ")[1:], strict=False)
    )
