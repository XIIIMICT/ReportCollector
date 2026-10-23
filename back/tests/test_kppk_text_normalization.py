"""Text normalization of the «КППК» copies: trimmed, single spaces."""

from src.kppk.constants import normalize_text


def test_normalize_text_trims_and_collapses_spaces() -> None:
    """Doubled, leading, trailing and non-breaking whitespace collapse to one."""
    assert normalize_text("  АО  «СНХЗ»  ") == "АО «СНХЗ»"
    assert normalize_text("Сода\tкальцинированная   марки Б") == "Сода кальцинированная марки Б"
    assert normalize_text("строка\nс переносом") == "строка с переносом"
    assert normalize_text("неразрывный\u00a0пробел") == "неразрывный пробел"


def test_normalize_text_keeps_meaningful_values_untouched() -> None:
    """A single internal space is part of the value, not a defect to remove."""
    assert normalize_text('АО "Алтайский шинный комбинат"') == 'АО "Алтайский шинный комбинат"'
    assert normalize_text("РФ") == "РФ"
    assert normalize_text("1.1.1") == "1.1.1"


def test_normalize_text_turns_blank_into_empty_string() -> None:
    """Whitespace only is "no value"; NULL is "no value" too."""
    assert normalize_text("   ") == ""
    assert normalize_text("\u00a0\t\n") == ""
    assert normalize_text(None) == ""
