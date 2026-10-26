"""Static reference data of the «КППК» project (project key and section ids)."""


def normalize_text(value: str | None) -> str:
    """
    Canonical form of a КППК text value: trimmed, single spaces, `;` as `,`.

    Names, codes and regions in the source dumps carry stray whitespace —
    leading/trailing spaces, tabs, line breaks, doubled and non-breaking spaces.
    None of it is meaningful, and keeping it would make lookups by display name
    (the matrix, the realization copies keyed by product NAME) miss. Every text
    value the seeder writes and every text cell a user saves goes through here,
    so the stored strings and the incoming ones always compare equal.

    The semicolon is folded into a comma because the dictionaries and the fact
    dumps disagree on it for the SAME product: `raw.kppk_d_product_seq` spells it
    `Прочая продукция (некондиция, восстановленные, РКО)` while the realization
    dump says `(некондиция; восстановленные; РКО)` — a difference of punctuation
    only, which used to make the copy abort on «неизвестный продукт».

    An empty result stays an empty string: for an optional field the caller
    decides whether that means NULL.
    """
    if value is None:
        return ""
    # `split()` on its own handles every Unicode whitespace, and `join` puts
    # exactly one ordinary space back — this also drops the non-breaking space
    # Excel loves to embed.
    return " ".join(value.split()).replace(";", ",")


KPPK_PROJECT_KEY = "kppk"
KPPK_PROJECT_NAME = "КППК"

# Source schema the seeder reads the original dumps from (as on the prod DB).
KPPK_SOURCE_SCHEMA_RAW = "raw"
KPPK_SOURCE_SCHEMA_DICT = "dict"

# Dictionary ids of the workspace tabs (match the frontend). The undo scopes
# and the Excel export sections are named by them; «Периоды» has no UI in this
# phase and takes part in neither.
KPPK_SECTION_PRODUCT_POSITIONS = "kppkPositions"
KPPK_SECTION_RAWS = "kppkRaws"
KPPK_SECTION_ORGS = "kppkOrgs"
# The project members dictionary (admin-only reads and mutations).
KPPK_SECTION_USERS = "kppkUsers"
KPPK_DICTIONARY_SECTIONS = (KPPK_SECTION_PRODUCT_POSITIONS, KPPK_SECTION_RAWS, KPPK_SECTION_ORGS)

# «Реализация» (Таблицы.xlsx): the editable main form. The collaboration room,
# undo scope and export section are named per slice — the plan table carries
# both plan slices while the fact table only the fact one, and each slice has
# its own cell keys (`kppk_realization/<row>/<field>` etc.).
KPPK_SECTION_REALIZATION_PLAN = "kppkRealizationPlan"
KPPK_SECTION_REALIZATION_FACT = "kppkRealizationFact"
KPPK_SECTION_COST = "kppkCost"
COST_FIELDS = (
    "fullCostBp",
    "productionCostBp",
    "variableCostBp",
    "fullCostOp",
    "productionCostOp",
    "variableCostOp",
    "fullCostFact",
    "productionCostFact",
    "variableCostFact",
)
COST_FIELDS = (
    "fullCostBp",
    "productionCostBp",
    "variableCostBp",
    "fullCostOp",
    "productionCostOp",
    "variableCostOp",
    "fullCostFact",
    "productionCostFact",
    "variableCostFact",
)

# The «Реализация» slices by their template label: Бизнес-план and Оперплан
# read the plan copy (the `_bp_plan` / `_max_plan` column families), Факт the
# fact copy.
KPPK_REALIZATION_SLICE_BP = "bp"
KPPK_REALIZATION_SLICE_OP = "op"
KPPK_REALIZATION_SLICE_FACT = "fact"
KPPK_REALIZATION_SLICES = (KPPK_REALIZATION_SLICE_BP, KPPK_REALIZATION_SLICE_OP, KPPK_REALIZATION_SLICE_FACT)
KPPK_REALIZATION_SLICE_LABELS: dict[str, str] = {
    KPPK_REALIZATION_SLICE_BP: "Бизнес-план",
    KPPK_REALIZATION_SLICE_OP: "Оперплан",
    KPPK_REALIZATION_SLICE_FACT: "Факт",
}

# Editable cell fields of each realization slice (camelCase API names). The
# column slots follow the template: Продукция | Рынок сбыта | Выкуп{Объем,
# Цена FCA} | Реализация{Объем, Цена FCA}.
KPPK_REALIZATION_PLAN_FIELDS: tuple[str, ...] = (
    "buyoutBpPlan",
    "fcaPriceBpPlan",
    "monthBpPlan",
    "fcaPriceMarketBpPlan",
    "buyoutMaxPlan",
    "fcaPriceMaxPlan",
    "monthMaxPlan",
    "fcaPriceMarketMaxPlan",
    "exciseBp",
    "exciseMaxPlan",
)
KPPK_REALIZATION_FACT_FIELDS: tuple[str, ...] = (
    "buyoutFact",
    "fcaPriceFact",
    "fact",
    "fcaPriceMarketFact",
    "exciseFact",
)

# The slice → (journal section, editable fields, DB decimal column per field).
# The fact slice stores its Объем in the fact copy; both plan slices share the
# plan copy.
KPPK_REALIZATION_SLICE_CONFIG: dict[str, str] = {
    KPPK_REALIZATION_SLICE_BP: KPPK_SECTION_REALIZATION_PLAN,
    KPPK_REALIZATION_SLICE_OP: KPPK_SECTION_REALIZATION_PLAN,
    KPPK_REALIZATION_SLICE_FACT: KPPK_SECTION_REALIZATION_FACT,
}

# Source tables copied into the program schema by the seeder.
KPPK_SOURCE_TABLES: dict[str, str] = {
    "kppk_d_org": "kppk_orgs",
    "kppk_d_product_seq": "kppk_products",
    "kppk_d_raw_seq": "kppk_raws",
    "kppk_f_realization": "kppk_realization",
    "kppk_f_realization_plan": "kppk_realization_plan",
    "kppk_f_production_plan": "kppk_production_plan",
}

# The market order of the source workbook. The markets themselves live in the
# `kppk_markets` dictionary (migration 0024 seeded it from this tuple, in this
# order); the constant stays as the reference for a fresh database and for tests.
MARKET_CODES: tuple[str, ...] = ("РФ", "ДЗ", "БЗ")

# Full wording of each market code, as the dictionary spells it. The source
# carried only the code; the name is the readable form the dictionary editor shows.
MARKET_NAMES: dict[str, str] = {
    "РФ": "Российская Федерация",
    "ДЗ": "Дальнее зарубежье",
    "БЗ": "Ближнее зарубежье",
}
