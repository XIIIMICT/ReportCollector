from .models import ProjectRole

LOCAL_USER_KEY = "local.user"

RAW_MATERIALS_PROJECT_KEY = "raw_materials"
RAW_MATERIALS_PROJECT_NAME = "Сырьё"

# Role names are hardcoded; the DB stores the project_role enum with ProjectRole values.
RAW_MATERIALS_ROLE_NAMES: dict[ProjectRole, str] = {
    ProjectRole.ADMIN: "Админ",
    ProjectRole.ASSET_WORKER: "Сотрудник актива",
}
