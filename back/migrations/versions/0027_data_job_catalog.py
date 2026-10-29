"""Describe the durable operation queue in the application data catalog."""

from alembic import op

revision = "0027_data_job_catalog"
down_revision = "0026_durable_data_jobs"
branch_labels = None
depends_on = None

COMMENTS = {
    "kind": "Тип фоновой операции",
    "user_id": "ID владельца задания",
    "cache_key": "Ключ повторного использования задания в пределах владельца",
    "payload": "Сериализованная спецификация запроса без строк таблицы",
    "status": "Состояние: pending, running, done или failed",
    "progress": "Количество обработанных строк",
    "total": "Количество строк в снимке выгрузки",
    "attempt": "Номер попытки выполнения",
    "lease_token": "Токен текущей попытки для защиты от устаревшего обработчика",
    "lease_until": "Срок действия аренды обработчика",
    "available_at": "Время доступности для следующей попытки",
    "finished_at": "Время завершения задания",
    "expires_at": "Время удаления задания и его файлов",
    "artifact": "Ключ файла текущей попытки",
    "size": "Размер готового файла в байтах",
    "error": "Сообщение об ошибке для пользователя",
    "error_code": "Класс ошибки для диагностики и политики повторов",
    "request_id": "ID исходного HTTP-запроса для корреляции логов",
}


def upgrade() -> None:
    op.create_table_comment("data_jobs", "Фоновые операции → Очередь, состояние и попытки формирования файлов")
    for column, comment in COMMENTS.items():
        op.alter_column("data_jobs", column, comment=comment)


def downgrade() -> None:
    op.drop_table_comment("data_jobs")
    for column in COMMENTS:
        op.alter_column("data_jobs", column, comment=None)
