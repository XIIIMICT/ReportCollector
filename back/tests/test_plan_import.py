"""Preflight of a technical-directorate Excel plan (no database writes)."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from openpyxl import Workbook
from pyxlsb import open_workbook

from src.renovation.import_plan import (
    _ASSET_HEADER,
    _CODE_HEADER,
    _norm,
    inspect_plan_file,
    inspect_plan_file_streaming,
)

if TYPE_CHECKING:
    from src.renovation.schemas import RenovationPlanImportResult

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
    for issue in (
        *result.invalid_data_types,
        *result.formula_reference_errors,
        *result.missing_dictionary_values,
        *result.missing_required_values,
    ):
        assert "строка" in issue.location, issue.location
    # A skipped row is the sheet's own 1-based row number.
    assert all(row_number > 0 for row_number in result.skipped_rows)
    # A bad code carries its row too: «CODE (строка 15)».
    for code in result.invalid_calculated_codes:
        assert "строка" in code, code
    # The status agrees with the findings: nothing to report means `ready`;
    # skipped rows alone do not fail the file unless every row was skipped.
    reported = (
        result.duplicate_codes
        or result.invalid_calculated_codes
        or result.invalid_data_types
        or result.formula_reference_errors
        or result.missing_required_values
        or result.missing_dictionary_values
        or result.unknown_assets
    )
    assert (result.status == "has_errors") is (bool(reported) or result.all_rows_skipped)


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


async def test_a_repeated_upload_skips_both_the_scan_and_the_analysis(client, monkeypatch) -> None:
    """The second upload of the same bytes is served from Valkey without opening the workbook."""
    from conftest import signed_identity_headers
    from fastapi import UploadFile

    from src.renovation import router as renovation_router
    from src.renovation.import_plan import PlanFileScope
    from src.renovation.inspect_cache import InspectResultCache
    from src.renovation.schemas import RenovationPlanImportResult

    class _FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
            del ex
            self.store[key] = value

    cache = InspectResultCache(_FakeRedis())
    monkeypatch.setattr(renovation_router, "get_inspect_cache", lambda: cache)
    calls = {"scan": 0, "inspect": 0}
    order: list[str] = []
    real_hash_upload = renovation_router._rewind_and_hash_upload

    async def counting_hash(file: UploadFile) -> str:
        order.append("hash")
        return await real_hash_upload(file)

    def fake_scan(*_args: object, **_kwargs: object) -> PlanFileScope:
        calls["scan"] += 1
        order.append("scan")
        return PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset())

    def fake_inspect(*_args: object, **_kwargs: object) -> RenovationPlanImportResult:
        calls["inspect"] += 1
        return RenovationPlanImportResult(status="ready", assets=["АО «БСЗ»"], new_rows=1)

    monkeypatch.setattr(renovation_router, "_rewind_and_hash_upload", counting_hash)
    monkeypatch.setattr(renovation_router, "scan_file_scope", fake_scan)
    monkeypatch.setattr(renovation_router, "inspect_plan_file_streaming", fake_inspect)

    for _ in range(2):
        response = await client.post(
            "/api/renovation/plan/import/inspect",
            files={"file": ("plan.xlsx", b"same-bytes", "application/octet-stream")},
            headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["new_rows"] == 1

    # One scope-discovery read and one analysis, both remembered for the repeat.
    assert calls == {"scan": 1, "inspect": 1}
    # The second upload reads the bytes for the hash ONLY: no workbook parse.
    assert order == ["hash", "scan", "hash"]


def test_assets_in_file_are_the_cache_scope() -> None:
    """The file's «Наименование ДО» codes decide which assets scope the cache."""
    from src.renovation.import_plan import assets_in_file

    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    assert assets_in_file(_SAMPLE) == frozenset({"bszr"})


def test_scan_file_scope_names_the_catalogs_the_file_validates() -> None:
    """The template's reference columns decide which dictionaries can invalidate it."""
    from src.renovation.import_plan import scan_file_scope

    if _SAMPLE is None:
        pytest.skip("no sample in docs/samples/")
    scope = scan_file_scope(_SAMPLE)
    assert scope.assets == frozenset({"bszr"})
    assert scope.assets_are_names is False
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
    # A deleted row that was not the newest changes the count, so the report is
    # re-analysed even when max(updated_at) stays the same.
    assert _import_cache_scope([first], stamp, row_count=10) != _import_cache_scope([first], stamp, row_count=9)
    assert _import_cache_scope([first], stamp, row_count=10) == _import_cache_scope([first], stamp, row_count=10)


def test_the_asset_lookup_uses_the_case_folded_scope_keys() -> None:
    """A code-based file must resolve its touched assets, not silently none."""
    from src.renovation.models import RAsset
    from src.renovation.router import _asset_scope_lookup

    asset = RAsset(code="BSZR", name="АО «БСЗ»")
    by_code = _asset_scope_lookup([asset], assets_are_names=False)
    by_name = _asset_scope_lookup([asset], assets_are_names=True)

    assert by_code["bszr"] is asset
    assert by_name['ао "бсз"'] is asset


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
    before = await _dictionary_fingerprint(db_session, file_scope=scope, touched_assets=[])

    db_session.add(RenovationReferenceValue(catalog=RReferenceCatalog.CRITICALITY, label="5"))
    await db_session.commit()
    unchanged = await _dictionary_fingerprint(db_session, file_scope=scope, touched_assets=[])
    assert unchanged == before

    db_session.add(RenovationReferenceValue(catalog=RReferenceCatalog.WORK_TYPE, label="Новый вид работ"))
    await db_session.commit()
    changed = await _dictionary_fingerprint(db_session, file_scope=scope, touched_assets=[])
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


def test_a_drop_without_the_code_column_is_matched_by_predpriyatie_name(xlsx_sample: Path, tmp_path: Path) -> None:
    """
    No «Наименование ДО»: the entity is identified by its «Предприятие» name.

    The name is matched against the asset dictionary case- and quote-insensitively
    (the file writes `АО "БСЗ"`, the dictionary «АО «БСЗ»»). The known name loads,
    an unknown one is a file error — never a silently dropped row.
    """
    from openpyxl import load_workbook

    book = load_workbook(xlsx_sample)
    sheet = book.active
    headers = [_norm(cell.value) for cell in sheet[1]]
    code_column = headers.index(_norm(_ASSET_HEADER)) + 1
    asset_column = headers.index(_norm("Предприятие")) + 1
    sheet.cell(row=1, column=code_column).value = "Служебное поле"
    target = tmp_path / "by-name.xlsx"
    book.save(target)

    known = inspect_plan_file(
        target,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values={},
    )
    assert known.status != "invalid_format"
    assert known.unknown_assets == []
    assert known.assets == ["АО «БСЗ»"]
    assert known.new_rows > 0

    book = load_workbook(target)
    sheet = book.active
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row=row, column=asset_column).value = "АО «Нет такого завода»"
    book.save(target)
    unknown = inspect_plan_file(
        target,
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values={},
    )
    assert unknown.status == "has_errors"
    assert unknown.unknown_assets
    assert all("строка" in issue.location for issue in unknown.unknown_assets)


def test_the_name_scope_says_whether_assets_are_names(xlsx_sample: Path, tmp_path: Path) -> None:
    """The cache scope carries asset keys and the flag the router resolves them with."""
    from openpyxl import load_workbook

    from src.renovation.import_plan import scan_file_scope

    by_code = scan_file_scope(xlsx_sample)
    assert by_code.assets_are_names is False
    assert by_code.assets == frozenset({"bszr"})

    book = load_workbook(xlsx_sample)
    sheet = book.active
    headers = [_norm(cell.value) for cell in sheet[1]]
    sheet.cell(row=1, column=headers.index(_norm(_ASSET_HEADER)) + 1).value = "Служебное поле"
    target = tmp_path / "scope-by-name.xlsx"
    book.save(target)

    by_name = scan_file_scope(target)
    assert by_name.assets_are_names is True
    # A name key: trimmed, case-folded, guillemets folded to a plain quote.
    assert by_name.assets == frozenset({'ао "бсз"'})


def test_a_date_typed_as_text_is_accepted(xlsx_sample: Path, tmp_path: Path) -> None:
    """
    A correct date stored as text is a date, not a «Неверный тип данных».

    The customer's files routinely declare a date column (or a single cell) as
    «Текстовый» while the values are real dates: the preflight must read the
    value, not the declared type.
    """
    from openpyxl import load_workbook

    from src.renovation.form_schema import field_meta, normalize_label

    date_meta = field_meta("col_024")
    assert date_meta is not None
    date_label = normalize_label(date_meta.label)
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


def test_a_readable_workbook_without_required_columns_is_a_content_error(tmp_path: Path) -> None:
    """
    An opened file is never «некорректного формата»: it is a plan with errors.

    The report names the headings the load cannot work without, so the operator
    knows what to add instead of being told the whole workbook is broken.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "РБ 2027"
    sheet.append([_CODE_HEADER, "Наименование производства", "Цех"])
    sheet.append(["Р-001", "Производство", "Цех"])
    path = tmp_path / "plan.xlsx"
    book.save(path)

    # The endpoint uses the streaming reader; both must agree on the answer.
    for inspect in (inspect_plan_file, inspect_plan_file_streaming):
        result = inspect(path, existing_codes=set(), asset_names_by_code={})

        assert result.status == "has_errors"
        assert result.missing_required_columns == [
            "Наименование ДО или Предприятие",
            "Группа критичности оборудования",
            "Категория работ",
            "Вид работ",
            "Отнесение затрат",
            "Способ выполнения работ",
            "Дата начала работ",
            "Дата окончания работ",
        ]


def test_missing_date_columns_are_named_in_the_report() -> None:
    """Both work dates are required columns from now on, not optional extras."""
    header = _PREFLIGHT_HEADER[:-2]
    row = _preflight_row()[:-2]

    result = _inspect([header, list(range(1, len(header) + 1)), row])

    assert result.status == "has_errors"
    assert result.missing_required_columns == ["Дата начала работ", "Дата окончания работ"]
    assert result.new_rows == 0


def test_an_empty_readable_sheet_reports_every_required_column() -> None:
    """A plan sheet with no rows at all is a content error, not a broken file."""
    result = _inspect([])

    assert result.status == "has_errors"
    assert len(result.missing_required_columns) == 9
    assert result.missing_required_columns[0] == _CODE_HEADER


def test_empty_required_values_are_reported_with_their_cells() -> None:
    """
    A present required column with blank cells is an error, not a heading issue.

    The cells are reported exactly; only a missing asset skips a row.
    """
    result = _inspect(
        [
            _PREFLIGHT_HEADER,
            list(range(1, len(_PREFLIGHT_HEADER) + 1)),
            _preflight_row("Р-001", start_date="   "),
            _preflight_row("Р-002", criticality=""),
            _preflight_row("Р-003"),
        ]
    )

    assert result.missing_required_columns == []
    assert result.status == "has_errors"
    assert result.skipped_rows == []
    assert result.new_rows == 1
    assert [(issue.location, issue.value) for issue in result.missing_required_values] == [
        ("O, строка 3", ""),
        ("D, строка 4", ""),
    ]


# A minimal but fully recognized template: the code and asset columns plus the
# required reference fields and both work dates. A row is skippable only by
# blanking the asset; any other blank required cell is an error.
_PREFLIGHT_HEADER: list[object] = [
    _CODE_HEADER,
    _ASSET_HEADER,
    "Предприятие",
    "Группа критичности оборудования",
    "Категория работ",
    "Вид работ",
    "Отнесение затрат",
    "Способ выполнения работ",
    "Наименование производства",
    "Цех",
    "Отделение/установка",
    "Класс оборудования",
    "Наименование ЕО",
    "Содержание работ",
    "Дата начала работ",
    "Дата окончания работ",
]


def _preflight_row(
    code: object = "Р-001",
    *,
    asset: object = "BSZR",
    criticality: object = "4",
    start_date: object = "2027-01-01",
    end_date: object = "2027-02-01",
) -> list[object]:
    """One valid data row; a test may blank any required cell or the asset."""
    return [
        code,
        asset,
        'АО "БСЗ"',
        criticality,
        "ТОиР",
        "ТО",
        "OPEX",
        "Собственные силы",
        "Производство",
        "Цех",
        "Отделение",
        "Класс",
        "ЕО",
        "Содержание",
        start_date,
        end_date,
    ]


def _inspect(matrix: list[list[object]]) -> RenovationPlanImportResult:
    """Run the preflight on a plain row matrix (no workbook round trip)."""
    from src.renovation.import_plan import _inspect_rows

    return _inspect_rows(
        iter(matrix),
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
        reference_values=None,
    )


def test_a_numbering_row_stored_as_text_is_skipped() -> None:
    """
    The helper row may arrive as text cells and must still not become a data row.

    Excel hands numbers over as strings when the row is formatted «Текстовый»;
    reading only the cell type made the counting row load with the code «1».
    """
    numbering: list[object] = [str(number) for number in range(1, len(_PREFLIGHT_HEADER) + 1)]
    result = _inspect([_PREFLIGHT_HEADER, numbering, _preflight_row()])

    assert result.status == "ready"
    assert result.new_rows == 1
    assert result.skipped_rows == []


def test_a_whitespace_only_optional_text_is_empty_not_a_type_error() -> None:
    """
    An optional text cell holding only spaces is EMPTY, like a blank cell.

    Text is stored trimmed, so whitespace must not reach the type check: it made
    «Влияние на производство» — a nullable field — report «неверный тип данных».
    """
    header = [*_PREFLIGHT_HEADER, "Влияние на производство"]
    result = _inspect(
        [
            header,
            list(range(1, len(header) + 1)),
            [*_preflight_row(), "   "],
        ]
    )

    assert result.status == "ready"
    assert result.invalid_data_types == []
    assert result.formula_reference_errors == []


def test_a_broken_formula_reference_is_its_own_error_category() -> None:
    """
    Excel's cached «#REF!» is a formula error, not a valid text/number value.

    In a text field it would be stored as ordinary text; in a number field it
    would be reported as a type error. Both hide the real cause, so the
    analysis reports the reference error separately.
    """
    header = [*_PREFLIGHT_HEADER, "Влияние на производство"]
    result = _inspect(
        [
            header,
            list(range(1, len(header) + 1)),
            [*_preflight_row(), "#REF!"],
        ]
    )

    assert result.status == "has_errors"
    assert [(issue.location, issue.value) for issue in result.formula_reference_errors] == [
        ("Q, строка 3", "#REF!"),
    ]
    assert result.invalid_data_types == []
    assert result.new_rows == 1


def test_a_broken_reference_in_a_calculated_column_is_still_reported() -> None:
    """Calculated columns are recomputed, but a broken formula is still an error."""
    header = [*_PREFLIGHT_HEADER, "Стоимость итого, руб. без НДС"]
    result = _inspect(
        [
            header,
            list(range(1, len(header) + 1)),
            [*_preflight_row(), "#REF!"],
        ]
    )

    assert result.status == "has_errors"
    assert [(issue.location, issue.value) for issue in result.formula_reference_errors] == [
        ("Q, строка 3", "#REF!"),
    ]


def test_a_blank_required_cell_is_an_error_not_a_skip() -> None:
    """Only a missing asset skips a row; a blank required reference is an error."""
    result = _inspect(
        [
            _PREFLIGHT_HEADER,
            list(range(1, len(_PREFLIGHT_HEADER) + 1)),
            _preflight_row("Р-001", criticality=None),
            _preflight_row("Р-002"),
            _preflight_row("Р-003", criticality=""),
        ]
    )

    assert result.status == "has_errors"
    assert result.new_rows == 1
    assert result.rows_to_update == 0
    assert result.skipped_rows == []
    assert result.all_rows_skipped is False
    assert [(issue.location, issue.value) for issue in result.missing_required_values] == [
        ("D, строка 3", ""),
        ("D, строка 5", ""),
    ]


def test_rows_without_an_asset_are_skipped_and_never_block_the_load() -> None:
    """
    The asset is the only skip criterion, and a skip is not an error.

    The operator loads the rest; the report only names what will not be written.
    """
    result = _inspect(
        [
            _PREFLIGHT_HEADER,
            list(range(1, len(_PREFLIGHT_HEADER) + 1)),
            _preflight_row("Р-001", asset=None),
            _preflight_row("Р-002"),
            _preflight_row("Р-003"),
            _preflight_row("Р-004", asset=""),
        ]
    )

    assert result.status == "ready"
    assert (result.new_rows, result.rows_to_update) == (2, 0)
    assert result.skipped_rows == [3, 6]
    assert result.all_rows_skipped is False
    assert result.missing_required_values == []


def test_a_row_without_a_work_code_is_an_error() -> None:
    """The code identifies the row: a blank one is an error, not a skip."""
    result = _inspect(
        [
            _PREFLIGHT_HEADER,
            list(range(1, len(_PREFLIGHT_HEADER) + 1)),
            _preflight_row(None),
            _preflight_row("Р-002"),
        ]
    )

    assert result.status == "has_errors"
    assert result.new_rows == 1
    assert result.skipped_rows == []
    assert [(issue.location, issue.value) for issue in result.missing_required_values] == [
        ("A, строка 3", ""),
    ]


def test_a_file_with_every_row_skipped_has_nothing_to_load() -> None:
    """Every row lacks the asset: the one case where skipping alone fails."""
    result = _inspect(
        [
            _PREFLIGHT_HEADER,
            list(range(1, len(_PREFLIGHT_HEADER) + 1)),
            _preflight_row("Р-001", asset=None),
            _preflight_row("Р-002", asset=""),
        ]
    )

    assert result.status == "has_errors"
    assert result.all_rows_skipped is True
    assert result.skipped_rows == [3, 4]
    assert result.new_rows == 0
    assert result.rows_to_update == 0
    assert result.missing_required_values == []
