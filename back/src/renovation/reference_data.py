"""Static reference options of the renovation plan (from renovation-form.json)."""

from uuid import UUID, uuid5

# Every seeded option gets a deterministic id: the same (catalog, label) pair
# always yields the same UUID, so a re-seed recognises its own rows and the ids
# stay identical across environments and installations. This replaces the old
# human-readable `value_key` column — the id itself is the stable identity.
_REFERENCE_NAMESPACE = UUID("6f1c8a4e-4f4b-4a3d-9c2e-6d0f5b1a7c93")


def reference_option_id(catalog: str, label: str) -> UUID:
    """Deterministic id of a seeded catalog option."""
    return uuid5(_REFERENCE_NAMESPACE, f"{catalog}:{label}")


REFERENCE_OPTIONS: list[tuple[str, str]] = [
    ("criticality", "1"),
    ("criticality", "2"),
    ("criticality", "3"),
    ("criticality", "4"),
    ("work_category", "ТОиР"),
    ("work_category", "Остановочный капитальный ремонт"),
    ("work_category", "Вспомогательные затраты"),
    ("work_category", "ЭПБ и ДО"),
    ("work_category", "ЗФиО"),
    ("work_category", "ПИР"),
    ("work_category", "Проект улучшения"),
    ("work_category", "Целевая программа"),
    ("work_type", "ТО"),
    ("work_type", "ТР"),
    ("work_type", "СР"),
    ("work_type", "КР"),
    ("work_type", "Чистка"),
    ("work_type", "Испытания"),
    ("work_type", "Поверка"),
    ("work_type", "Калибровка"),
    ("work_type", "Измерения"),
    ("work_type", "Корректирующие работы"),
    ("work_type", "Работы по фактическому тех.состоянию"),
    ("work_type", "Вспомогательные работы (леса, изоляция и пр)"),
    ("work_type", "Оснащенность ремонтного персонала"),
    ("work_type", "ТОиР непроизводственных объектов предприятия"),
    ("work_type", "Расходы без влияния на механическую готовность"),
    ("work_type", "Экспертиза"),
    ("work_type", "Тех. освидетельствование"),
    ("work_type", "Диагностические обследования"),
    ("work_type", "Инспекции"),
    ("work_type", "Замена физически изношенного оборудования"),
    ("work_type", "Реконструкция"),
    ("work_type", "Модернизация"),
    ("work_type", "Техническое перевооружение"),
    ("work_type", "Капитальное строительство"),
    ("cost_allocation", "CAPEX"),
    ("cost_allocation", "OPEX"),
    ("cost_allocation", "Счет 91"),
    ("execution_method", "Собственные силы"),
    ("execution_method", "Химремонт"),
    ("execution_method", "НХД"),
    ("execution_method", "УСО"),
]


def reference_option_ids() -> dict[tuple[str, str], UUID]:
    """Deterministic ids keyed by (catalog, label) — the seed's lookup table."""
    return {(catalog, label): reference_option_id(catalog, label) for catalog, label in REFERENCE_OPTIONS}
