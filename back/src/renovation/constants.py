from .models import RProjectRole

RENOVATION_PROJECT_KEY = "renovation"
RENOVATION_PROJECT_NAME = "Ремонт"

# Role names are hardcoded; the DB stores the r_project_role enum with RProjectRole values.
RENOVATION_ROLE_NAMES: dict[RProjectRole, str] = {
    RProjectRole.ADMIN: "Администратор",
    RProjectRole.APPROVER: "Утверждающий",
    RProjectRole.BLOCK_OWNER: "Владелец блока",
    RProjectRole.ASSET_WORKER: "Сотрудник актива",
    RProjectRole.BLOCKED: "Заблокирован",
}

# Renovation assets (legal entities) from the legacy renovation-form.json:
# (code, repairCode, name). The repair code is the official four-letter code
# used in the renovation ID (e.g. «VOZR» for АО «Волжский Оргсинтез»).
R_ASSETS: list[tuple[str, str, str]] = [
    ("bsk", "BSKR", "АО «БСК»"),
    ("bsz", "BSZR", "АО «БСЗ»"),
    ("sz", "KSZR", "АО «СЗ»"),
    ("ti", "TIAF", "ООО «Титановые Инвестиции»"),
    ("snhz", "SNHZ", "АО «СНХЗ»"),
    ("ks", "KUCH", "АО «Кучуксульфат»"),
    ("vos", "VOZR", "АО «Волжский Оргсинтез»"),
    ("mk", "MFKR", "АО «Метафракс Кемикалс»"),
    ("metadynea", "MDRU", "ООО «Метадинеа»"),
    ("soda-chlorate", "SXRU", "ООО «Сода-хлорат»"),
    ("dgok", "DGOK", "ООО «Дальнегорский ГОК»"),
    ("minudobreniya", "MINU", "АО «Минудобрения»"),
    ("ashk", "ASHK", "АО ПО «Алтайский шинный комбинат»"),
    ("khimremont", "HIMR", "ООО «Химремонт»"),
    ("nhd", "NHDR", "ООО «Нефтехимдиагностика»"),
    ("rh-supply", "SNAB", "ООО «РХ Снабжение»"),
    ("nhrs", "NHRS", "ООО «Нефтехимремстрой»"),
]
