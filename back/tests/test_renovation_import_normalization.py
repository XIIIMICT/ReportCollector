"""«Лот» gets its trailing dot on import; the code is loaded as-is."""

from datetime import date

from src.renovation.import_plan import _coerce, _trimmed_text
from src.renovation.renovation_id import normalize_direction_code


def test_the_lo_t_code_gets_its_trailing_dot() -> None:
    """`1.1.1` and `1.1.1.` are the same lot code — the dot is added silently."""
    assert normalize_direction_code("1.1.1") == "1.1.1."
    assert normalize_direction_code("1.1.1.") == "1.1.1."
    assert normalize_direction_code(" 2.3.1 ") == "2.3.1."


def test_text_cells_are_trimmed_and_empty_is_null() -> None:
    assert _coerce("  код ремонта  ", "text") == "код ремонта"
    assert _coerce("   ", "text") is None
    assert _coerce("\u00a0значение\u00a0", "text") == "значение"
    assert _trimmed_text("") is None


def test_the_repair_code_is_loaded_verbatim() -> None:
    """The code is not rebuilt from its parts: whatever the file says is stored."""
    assert _coerce("2027_BSZR_1.3.1_0706179", "text") == "2027_BSZR_1.3.1_0706179"
    assert _coerce("  какой угодно код  ", "text") == "какой угодно код"


def test_a_date_cell_still_parses() -> None:
    assert _coerce(date(2027, 1, 1), "date") == "2027-01-01"


def test_a_date_cell_declared_as_text_still_parses() -> None:
    """The workbook's cell type is not trusted: a text date is a date too."""
    assert _coerce("05.11.2003", "date") == "2003-11-05"
    assert _coerce(" 5.11.2003 ", "date") == "2003-11-05"
    assert _coerce("05.11.03", "date") == "2003-11-05"
    assert _coerce("05/11/2003", "date") == "2003-11-05"
    assert _coerce("2003-11-05", "date") == "2003-11-05"
    # A text cell may carry a time even though the column is a plain date.
    assert _coerce("05.11.2003 13:45", "date") == "2003-11-05"
    assert _coerce("05.11.2003 13:45:10", "date") == "2003-11-05"
    assert _coerce("2003-11-05T13:45:10", "date") == "2003-11-05"


def test_a_text_that_is_not_a_real_date_stays_a_type_error() -> None:
    assert _coerce("31.02.2003", "date") is None
    assert _coerce("2003-13-05", "date") is None
    assert _coerce("не дата", "date") is None
    assert _coerce("", "date") is None
