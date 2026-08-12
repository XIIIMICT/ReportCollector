from .models import ProjectRole

LOCAL_USER_KEY = "local.user"

RAW_MATERIALS_PROJECT_KEY = "raw_materials"
RAW_MATERIALS_PROJECT_NAME = "Сырьё"

# Имена ролей заданы хардкодом; в БД хранится enum project_role со значениями ProjectRole.
RAW_MATERIALS_ROLE_NAMES: dict[ProjectRole, str] = {
    ProjectRole.ADMIN: "Администратор",
    ProjectRole.ASSET_WORKER: "Работник по активам",
}
