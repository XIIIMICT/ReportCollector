"""Preflight of a technical-directorate XLSB plan (no database writes)."""

from pathlib import Path

import pytest
from pyxlsb import open_workbook

from src.renovation.import_plan import _norm, inspect_plan_file

# The checked-in technical-directorate plan sample (`docs/samples/`): the real
# workbook the importer is matched against. Committed on purpose — it is small
# (48 KB) and makes the format tests meaningful instead of skipped.
_SAMPLE = Path(__file__).resolve().parents[2] / "docs" / "samples" / "renovation_td_example.xlsb"
if not _SAMPLE.exists():  # a checkout without the sample keeps the tests skipped
    _SAMPLE = None


def test_real_template_is_recognised() -> None:
    """The sample carries every heading the analyzer loads, incl. the aliases."""
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
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


def test_sample_analyses_without_being_rejected() -> None:
    """
    The sample is a VALID template, whatever rows it carries.

    The committed workbook is a working example that may or may not contain row
    errors — both are legitimate states of a customer file. What must hold is
    that the analyzer recognises the format (never `invalid_format`), reads the
    asset from «Наименование ДО», and finds rows to load.
    """
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
    )
    assert result.status != "invalid_format", "the sample template is rejected by the analyzer"
    assert result.status in {"ready", "has_errors"}
    assert result.assets == ["АО «БСЗ»"]
    assert result.new_rows > 0


def test_every_reported_problem_points_at_a_row() -> None:
    """
    Whatever errors the sample contains, each is reported with its location.

    This is the invariant the operator relies on: a rejection must be actionable.
    A clean file simply reports nothing — the assertions hold either way.
    """
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values={"criticality": {"1", "2", "3", "4"}},
    )
    # A duplicate code names the sheet rows it occupies: «CODE (строки 3, 9)».
    for duplicate in result.duplicate_codes:
        assert "строк" in duplicate, duplicate
    # A cell-level issue carries «<column>, строка <n>».
    for issue in (*result.invalid_data_types, *result.missing_dictionary_values, *result.blank_values):
        assert "строка" in issue.location, issue.location
    # A bad code carries its row too: «CODE (строка 15)».
    for code in result.invalid_calculated_codes:
        assert "строка" in code, code
    # The status agrees with the findings: nothing to report means `ready`.
    reported = (
        result.duplicate_codes
        or result.invalid_calculated_codes
        or result.invalid_data_types
        or result.missing_dictionary_values
        or result.blank_values
        or result.unknown_assets
    )
    assert (result.status == "has_errors") is bool(reported)


def test_an_unknown_asset_makes_the_file_invalid() -> None:
    """The asset is a reference value: a code the dictionary lacks is an error."""
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    # No assets known to the project: every row names «BSZR».
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={},
        reference_values={"criticality": {"1", "2", "3", "4"}},
    )
    assert result.status == "has_errors"
    assert result.unknown_assets
    assert all(issue.value.upper() == "BSZR" for issue in result.unknown_assets)
    # The known-dictionary pass reports no asset errors at all.
    known = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values={"criticality": {"1", "2", "3", "4"}},
    )
    assert known.unknown_assets == []


def test_duplicate_codes_name_every_sheet_row() -> None:
    """Repeated work codes are reported once with all the rows they occupy."""
    from src.renovation import import_plan

    assert import_plan._duplicates_with_rows(["A", "B", "A"], [3, 4, 9]) == ["A (строки 3, 9)"]


def test_the_archived_correctness_check_never_fires() -> None:
    """
    The ID formula is off, so no code is ever reported as «некорректно рассчитанный».

    Independent of what the sample contains: with the generator disabled the
    analyzer loads the codes as-is, so that list stays empty even when the file
    has other problems (duplicates, bad types, unknown values).
    """
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    result = inspect_plan_file(
        _SAMPLE,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
    )
    assert result.invalid_calculated_codes == []


def test_sample_is_uploaded_through_the_api(tmp_path: Path) -> None:
    """The multipart upload must reach the analyzer intact (bytes + boundary)."""
    from fastapi.testclient import TestClient

    from src.main import app

    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
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
        pytest.skip("no sample in docs/samples/")
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
        pytest.skip("no sample in docs/samples/")
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
