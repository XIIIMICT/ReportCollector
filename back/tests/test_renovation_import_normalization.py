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
