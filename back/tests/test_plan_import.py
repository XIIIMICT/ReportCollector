"""Preflight of a technical-directorate XLSB plan (no database writes)."""

from pathlib import Path

import pytest
from pyxlsb import open_workbook

from src.renovation.import_plan import _norm, inspect_plan_file

# The checked-in «РБ ПОФ» sample: the template we match by column NAMES.
_SAMPLE = next(Path(__file__).resolve().parents[2].joinpath("docs").glob("*.xlsb"), None)


def test_real_template_is_recognised() -> None:
    """The sample carries every heading the analyzer loads, incl. the aliases."""
    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    with open_workbook(str(_SAMPLE)) as workbook, workbook.get_sheet(workbook.sheets[0]) as worksheet:
        headers = [cell.v for cell in next(worksheet.rows())]
    normalized = [_norm(value) for value in headers]
    for required in (
        "Наименование ДО",
        "Код работы",
        "Лот",
        "Предприятие",
        "Категория работ",
        "Вид работ",
        "Отнесение затрат",
        "Способ выполнения работ",
        "Дефектная ведомость/ВОР",
    ):
        wanted = _norm(required)
        assert any(item == wanted or item.startswith(wanted) for item in normalized), required


def test_sample_analyses_as_has_errors_not_invalid_format() -> None:
    """The sample must not be rejected as a wrong template — it has row errors."""
    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
    )
    assert result.status != "invalid_format", "the sample template is rejected by the analyzer"
    assert result.assets == ["АО «БСЗ»"]
    assert result.new_rows > 0


def test_duplicate_codes_name_every_sheet_row() -> None:
    """Repeated work codes are reported once with all the rows they occupy."""
    from src.renovation import import_plan

    assert import_plan._duplicates_with_rows(["A", "B", "A"], [3, 4, 9]) == ["A (строки 3, 9)"]


def test_the_sample_reports_duplicates_but_no_longer_bad_codes() -> None:
    """
    The archived correctness check does not fire; duplicates still do.

    The sample's codes are the legacy generated ones, and with the formula off
    the analyzer loads them as-is. Before the change every row mismatching the
    generator was listed as «некорректно рассчитанный код»; now that list is
    empty and the duplicates («Дублирующиеся коды») remain the only blocker.
    """
    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
    )
    assert result.invalid_calculated_codes == []
    assert result.duplicate_codes, "the sample is expected to carry repeated codes"


def test_sample_is_uploaded_through_the_api(tmp_path: Path) -> None:
    """The multipart upload must reach the analyzer intact (bytes + boundary)."""
    from fastapi.testclient import TestClient

    from src.main import app

    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    client = TestClient(app)
    with _SAMPLE.open("rb") as stream:
        response = client.post(
            "/api/renovation/plan/import/inspect",
            files={"file": (_SAMPLE.name, stream, "application/octet-stream")},
        )
    # Without an identity the endpoint refuses — but never with a body-parsing
    # 422, which would mean the multipart upload itself is broken.
    assert response.status_code in {401, 403, 200}, response.text


async def test_sample_upload_returns_the_preflight_report(client) -> None:
    """Full endpoint path: the real sample is accepted and its errors reported."""
    from conftest import signed_identity_headers

    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    response = await client.post(
        "/api/renovation/plan/import/inspect",
        files={"file": (_SAMPLE.name, _SAMPLE.read_bytes(), "application/octet-stream")},
        headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # The template is recognised: a wrong-format answer would mean the columns
    # were not matched by name.
    assert body["status"] != "invalid_format", body


def test_assets_in_file_are_the_cache_scope() -> None:
    """The file's «Наименование ДО» codes decide which assets scope the cache."""
    from src.renovation.import_plan import assets_in_file

    if _SAMPLE is None:
        pytest.skip("no template sample in docs/")
    assert assets_in_file(_SAMPLE) == frozenset({"BSZR"})


def test_assets_in_file_tolerates_a_broken_workbook(tmp_path: Path) -> None:
    from src.renovation.import_plan import assets_in_file

    path = tmp_path / "broken.xlsb"
    path.write_bytes(b"not a workbook")
    assert assets_in_file(path) == frozenset()


def test_scope_is_stable_for_the_same_assets_and_change_time() -> None:
    """Same assets + same last change → same scope; a newer edit → another scope."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.renovation.router import _import_cache_scope

    first, second = uuid4(), uuid4()
    stamp = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
    later = datetime(2026, 10, 23, 11, 0, tzinfo=UTC)

    assert _import_cache_scope([first, second], stamp) == _import_cache_scope([second, first], stamp)
    assert _import_cache_scope([first], stamp) != _import_cache_scope([first], later)
    assert _import_cache_scope([first], stamp) != _import_cache_scope([first, second], stamp)
    assert _import_cache_scope([], stamp) == ""
    assert _import_cache_scope([first], None).endswith("@none")


def test_sample_defect_numbers_keep_seven_digits() -> None:
    """The ДВ number loses its leading zero in Excel and is restored for the ID."""
    from src.renovation.import_plan import _defect_text

    assert _defect_text(706179.0) == "0706179"
    assert _defect_text("706179") == "0706179"
    assert _defect_text("0706179") == "0706179"
    assert _defect_text(None) == ""


def test_headers_are_matched_by_name_not_position() -> None:
    """Aliases resolve by their normalized heading, independent of order."""
    from src.renovation import import_plan

    mapping = import_plan._header_map()
    assert mapping[_norm("Отнесение затрат")] == "col_019"
    assert mapping[_norm("Способ выполнения работ")] == "col_020"
    assert mapping[_norm("Наименование производства")] == "col_004"
    assert mapping[_norm("Предприятие")] == "col_002"
    # Ignored headings never resolve to a field: they are skipped by name.
    assert all(_norm(name) not in mapping for name in ("Год", "№ п/п", "Код работы"))


def test_non_xlsb_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plan.xlsx"
    path.write_bytes(b"not a workbook")
    assert inspect_plan_file(path, existing_codes=set(), asset_names_by_code={}).status == "invalid_format"


def test_broken_workbook_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plan.xlsb"
    path.write_bytes(b"broken")
    assert inspect_plan_file(path, existing_codes=set(), asset_names_by_code={}).status == "invalid_format"
