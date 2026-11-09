"""Preflight of a technical-directorate Excel plan (no database writes)."""

from pathlib import Path

import pytest
from openpyxl import Workbook
from pyxlsb import open_workbook

from src.renovation.import_plan import _norm, inspect_plan_file

# The checked-in technical-directorate plan sample (`docs/samples/`): the real
# workbook the importer is matched against. Committed on purpose — it is small
# (48 KB) and makes the format tests meaningful instead of skipped.
_SAMPLE = Path(__file__).resolve().parents[2] / "docs" / "samples" / "renovation_td_example.xlsb"
if not _SAMPLE.exists():  # a checkout without the sample keeps the tests skipped
    _SAMPLE = None


@pytest.fixture(scope="session")
def xlsx_sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Convert the committed sample to OOXML at runtime.

    The customer ships either container, and the two readers must agree. A second
    committed binary would only add a second fixture to keep in sync, so the
    workbook is regenerated from the real `.xlsb` instead.
    """
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    target = tmp_path_factory.mktemp("xlsx-sample") / "renovation_td_example.xlsx"
    with open_workbook(str(_SAMPLE)) as workbook:
        sheet_name = workbook.sheets[0]
        with workbook.get_sheet(sheet_name) as worksheet:
            rows = [[cell.v for cell in row] for row in worksheet.rows()]
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    book.save(target)
    return target


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


async def test_sample_upload_returns_the_preflight_report(client, xlsx_sample: Path) -> None:
    """Full endpoint path: the real sample is accepted and its errors reported."""
    from conftest import signed_identity_headers

    for sample_path in (_SAMPLE, xlsx_sample):
        if sample_path is None:
            pytest.skip("no sample in docs/samples/")
        response = await client.post(
            "/api/renovation/plan/import/inspect",
            files={"file": (sample_path.name, sample_path.read_bytes(), "application/octet-stream")},
            headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # The template is recognised: a wrong-format answer would mean the columns
        # were not matched by name. Both containers go through the same reader.
        assert body["status"] != "invalid_format", body


def test_assets_in_file_are_the_cache_scope() -> None:
    """The file's «Наименование ДО» codes decide which assets scope the cache."""
    from src.renovation.import_plan import assets_in_file

    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    assert assets_in_file(_SAMPLE) == frozenset({"BSZR"})


def test_scan_file_scope_names_the_catalogs_the_file_validates() -> None:
    """The template's reference columns decide which dictionaries can invalidate it."""
    from src.renovation.import_plan import scan_file_scope

    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    scope = scan_file_scope(_SAMPLE)
    assert scope.assets == frozenset({"BSZR"})
    assert scope.catalogs == frozenset(
        {"criticality", "work_category", "work_type", "cost_allocation", "execution_method"}
    )


def test_assets_in_file_tolerates_a_broken_workbook(tmp_path: Path) -> None:
    from src.renovation.import_plan import assets_in_file, scan_file_scope

    path = tmp_path / "broken.xlsb"
    path.write_bytes(b"not a workbook")
    assert assets_in_file(path) == frozenset()
    assert scan_file_scope(path).catalogs == frozenset()


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


def test_scope_carries_dictionary_tokens() -> None:
    """A changed dictionary must re-key the report even with the plan untouched."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.renovation.router import _import_cache_scope

    asset = uuid4()
    stamp = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
    before = _import_cache_scope([asset], stamp, dictionaries="work_type=12@2026-10-23T09:00:00+00:00")
    after = _import_cache_scope([asset], stamp, dictionaries="work_type=13@2026-10-23T10:30:00+00:00")
    assert before != after
    # The dictionary is part of the key even when the file names no known asset.
    assert _import_cache_scope([], stamp, dictionaries="work_type=12@2026-10-23T09:00:00+00:00") != ""


async def test_dictionary_fingerprint_tracks_only_the_catalogs_the_file_uses(db_session) -> None:
    """
    Adding an option to a used catalog re-keys the report; an unused one doesn't.

    This is the cache-invalidation contract: a report that called a value
    missing must be re-analysable as soon as that value exists, while an edit to
    a catalog the file has no column for keeps the cached report valid.
    """
    from src.renovation.import_plan import PlanFileScope
    from src.renovation.plan import RenovationReferenceValue, RReferenceCatalog
    from src.renovation.router import _dictionary_fingerprint

    scope = PlanFileScope(assets=frozenset({"BSZR"}), catalogs=frozenset({"work_type"}))
    before = await _dictionary_fingerprint(db_session, file_scope=scope, assets_by_code={})

    db_session.add(RenovationReferenceValue(catalog=RReferenceCatalog.CRITICALITY, label="5"))
    await db_session.commit()
    unchanged = await _dictionary_fingerprint(db_session, file_scope=scope, assets_by_code={})
    assert unchanged == before

    db_session.add(RenovationReferenceValue(catalog=RReferenceCatalog.WORK_TYPE, label="Новый вид работ"))
    await db_session.commit()
    changed = await _dictionary_fingerprint(db_session, file_scope=scope, assets_by_code={})
    assert changed != before


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


def test_the_xlsx_container_is_analyzed_identically(xlsx_sample: Path) -> None:
    """The same workbook as OOXML yields the same report as the binary original."""
    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    binary = inspect_plan_file(_SAMPLE, existing_codes=set(), asset_names_by_code={"BSZR": "АО «БСЗ»"})
    ooxml = inspect_plan_file(xlsx_sample, existing_codes=set(), asset_names_by_code={"BSZR": "АО «БСЗ»"})
    assert ooxml.model_dump() == binary.model_dump()


def test_a_date_typed_as_text_is_accepted(xlsx_sample: Path, tmp_path: Path) -> None:
    """
    A correct date stored as text is a date, not a «Неверный тип данных».

    The customer's files routinely declare a date column (or a single cell) as
    «Текстовый» while the values are real dates: the preflight must read the
    value, not the declared type.
    """
    from openpyxl import load_workbook

    from src.renovation.form_schema import field_meta, normalize_label

    date_label = normalize_label(field_meta("col_024").label)
    book = load_workbook(xlsx_sample)
    sheet = book.active
    header_row = 1
    column = next(
        index
        for index in range(1, sheet.max_column + 1)
        if isinstance(sheet.cell(row=header_row, column=index).value, str)
        and normalize_label(sheet.cell(row=header_row, column=index).value) == date_label
    )
    row = next(
        number
        for number in range(header_row + 1, sheet.max_row + 1)
        if isinstance(sheet.cell(row=number, column=column).value, (int, float))
        and sheet.cell(row=number, column=column).value > 20000
    )
    # The date cell is now plain text, as Excel writes it in a «Текстовый» column.
    sheet.cell(row=row, column=column).value = "05.11.2003"
    target = tmp_path / "text-date.xlsx"
    book.save(target)

    result = inspect_plan_file(
        target,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values={},
    )
    assert result.status == "ready"
    assert result.invalid_data_types == []


def test_an_unsupported_suffix_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plan.csv"
    path.write_bytes(b"not a workbook")
    assert inspect_plan_file(path, existing_codes=set(), asset_names_by_code={}).status == "invalid_format"


def test_broken_workbook_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plan.xlsb"
    path.write_bytes(b"broken")
    assert inspect_plan_file(path, existing_codes=set(), asset_names_by_code={}).status == "invalid_format"
