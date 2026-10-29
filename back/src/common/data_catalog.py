"""Russian data-catalog descriptions for application-owned PostgreSQL objects."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Table

TABLE_COMMENTS: dict[str, str] = {
    "data_jobs": "Фоновые операции → Очередь, состояние и попытки формирования файлов",
    "app_users": "Общие учётные записи пользователей всех проектов.",
    "projects": "Проекты системы: Сырьё, Ремонт и КППК.",
    "user_roles": "Роли и доступ пользователей к проектам Сырьё и КППК.",
    "r_assets": "Ремонт → Справочник предприятий (активов).",
    "r_user_roles": "Ремонт → Роли, дирекции и доступ пользователей к проекту.",
    "r_user_assets": "Ремонт → Доступ пользователей к предприятиям.",
    "r_reference_values": "Ремонт → Значения пяти справочников; тип справочника задаётся полем catalog.",
    "r_plans": "Ремонт → Версии пообъектного плана: рабочая, на согласовании и архивная.",
    "r_plan_rows": "Ремонт → Строки пообъектного плана; col_* соответствуют столбцам формы.",
    "rm_assets": "Сырьё → Справочник активов (предприятий).",
    "rm_raw_materials": "Сырьё → Справочник сырьевых позиций.",
    "rm_delivery_bases": "Сырьё → Справочник базисов поставки.",
    "rm_asset_raw_materials": "Сырьё → Связи активов и сырьевых позиций, общие для Движения и Цен.",
    "rm_user_assets": "Сырьё → Доступ пользователей к активам.",
    "rm_movement_periods": "Сырьё → Открытые отчётные месяцы формы Движение.",
    "rm_material_movements": "Сырьё → Движение сырья по активу, сырьевой позиции и отчётному месяцу.",
    "rm_raw_material_prices": "Сырьё → Цены, бизнес-план и норматив запаса по связи актив–сырьё.",
    "rm_report_snapshots": "Сырьё → Зафиксированные версии Движения за отчётные месяцы.",
    "rm_snapshot_material_movements": "Сырьё → Архивные значения Движения; имена и показатели сохранены независимо от рабочих справочников.",
    "kppk_orgs": "КППК → Справочник ДЗО; копия raw.kppk_d_org с прикладным UUID.",
    "kppk_products": "КППК → Продуктовые позиции; копия raw.kppk_d_product_seq и дополнения из матрицы продуктов.",
    "kppk_raws": "КППК → Сырьевые позиции; копия raw.kppk_d_raw_seq.",
    "kppk_org_products": "КППК → Связи продуктовых позиций с ДЗО по матрице продуктов.",
    "kppk_markets": "КППК → Справочник рынков сбыта формы «Реализация» (РФ, ДЗ, БЗ).",
    "kppk_realization": "КППК → Реализация, фактический срез; копия raw.kppk_f_realization с календарным месяцем и UUID ДЗО.",
    "kppk_realization_plan": "КППК → Реализация, бизнес-план и оперплан; копия raw.kppk_f_realization_plan.",
    "kppk_production_plan": "КППК → План производства; копия raw.kppk_f_production_plan.",
    "kppk_cost": "КППК → Себестоимость по ДЗО, месяцу и продукту, без разреза рынков сбыта.",
    "collab_cell_states": "Совместное редактирование → Ревизии ячеек и последний автор для разрешения конфликтов undo/redo.",
    "collab_edit_stacks": "Совместное редактирование → Транзакционная история undo/redo по пользователю, сессии и форме.",
    "collab_outbox": "Совместное редактирование → Зафиксированные события, ожидающие публикации в WebSocket-комнаты.",
}

COMMON_COLUMNS = {
    "id": "Уникальный идентификатор записи (UUID).",
    "created_at": "Дата и время создания записи.",
    "updated_at": "Дата и время последнего изменения записи.",
    "created_by": "Имя автора создания записи.",
    "updater_user_id": "ID автора последнего изменения записи → app_users.id",
    "user_id": "Идентификатор пользователя → app_users.id.",
    "project_id": "Идентификатор проекта → projects.id.",
    "is_active": "Признак активности записи.",
    "raw_material_id": "Идентификатор сырьевой позиции → rm_raw_materials.id.",
    "asset_raw_material_id": "Идентификатор связи актив–сырьё → rm_asset_raw_materials.id.",
    "delivery_basis_id": "Идентификатор базиса поставки → rm_delivery_bases.id; NULL — базис не задан.",
    "report_month": "Отчётный месяц; дата первого дня календарного месяца.",
    "org_id": "Идентификатор ДЗО → kppk_orgs.id (UUID, не исходный числовой код).",
    "product_id": "Идентификатор продуктовой позиции → kppk_products.id.",
    "period_date": "Период КППК; дата первого дня календарного месяца.",
    "rep_date": "Отчётная дата исходной загрузки КППК (raw.rep_date).",
    "load_id": "Идентификатор загрузки в исходной системе КППК (raw.load_id).",
    "product": "Наименование продукта из исходной системы КППК.",
    "region": "Рынок сбыта КППК (РФ, ДЗ, БЗ); исходное поле region.",
    "lastdate": "Дата и время изменения в исходной системе КППК (raw.lastdate); без часового пояса.",
    "position": "Порядковая позиция строки внутри версии пообъектного плана.",
    "revision": "Ревизия строки; увеличивается при сохранении изменений.",
    "baseline_generation": "Поколение базовой версии плана для выделения новых и изменённых строк.",
    "plan_id": "Идентификатор версии пообъектного плана → r_plans.id.",
}

MOVEMENT_COLUMNS = {
    "business_plan_tons": "Бизнес-план, т; значение из формы Цены на момент фиксации.",
    "demand_plan_tons": "Потребность → План, т.",
    "consumption_fact_tons": "Потребность → Фактическое потребление, т.",
    "demand_deviation_tons": "Потребность → Отклонение план–факт, т.",
    "delivery_plan_tons": "Поставка → План, т.",
    "delivery_fact_tons": "Поставка → Факт, т.",
    "delivery_deviation_tons": "Поставка → Отклонение план–факт, т.",
    "balance_start_tons": "Остаток на начало месяца, т.",
    "balance_on_site_tons": "Остаток → На площадке, т.",
    "balance_in_transit_tons": "Остаток → В пути, т.",
    "balance_end_tons": "Остаток на конец месяца, т.",
    "storage_standard_tons": "Норматив запаса сырья, т.",
    "deviation_reason": "Причина отклонения показателей движения сырья.",
}

SPECIFIC_COLUMNS: dict[str, dict[str, str]] = {
    "data_jobs": {
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
    },
    "app_users": {
        "oidc_sub": "Идентификатор пользователя sub, полученный от OIDC/Keycloak.",
        "preferred_username": "Имя входа preferred_username, полученное от OIDC/Keycloak.",
        "email": "Адрес электронной почты; хранится в нижнем регистре, уникален.",
        "name": "Фамилия, имя и отчество пользователя.",
        "email_verified": "Признак подтверждения электронной почты провайдером идентификации.",
        "password_hash": "Хеш локального пароля; не открытый пароль. При SSO пароль управляется Keycloak.",
        "last_authenticated_at": "Время последней успешной аутентификации.",
        "is_deleted": "Глобальная блокировка/мягкое удаление учётной записи; не флаг доступа к отдельному проекту.",
        "theme": "Сохранённая тема интерфейса: system, light или dark.",
    },
    "projects": {
        "key": "Системный ключ проекта: raw_materials, renovation или kppk.",
        "name": "Название проекта в интерфейсе.",
    },
    "user_roles": {
        "role": "Роль в проекте: admin или asset_worker.",
        "is_active": "Разрешён ли пользователю доступ к этому проекту.",
    },
    "r_user_roles": {
        "role": "Роль в Ремонте: admin, approver, block_owner или asset_worker.",
        "block": "Дирекция ответственности: tech — техническая, ksir — КСиР, mto — МТО.",
        "is_active": "Разрешён ли пользователю доступ к проекту Ремонт.",
    },
    "r_assets": {
        "code": "Системный код предприятия для импорта исходных планов.",
        "repair_code": "Код предприятия в идентификационном коде работы/ремонта.",
        "name": "Наименование предприятия.",
    },
    "r_reference_values": {
        "catalog": "Тип справочника: criticality, work_category, work_type, cost_allocation или execution_method.",
        "label": "Отображаемое наименование справочного значения; уникально внутри справочника без учёта регистра.",
        "deleted_at": "Время удаления записи; NULL — запись не удалена.",
    },
    "r_plans": {
        "state": "Состояние версии: working — рабочая, pending_approval — на согласовании, archived — архивная."
    },
    "rm_assets": {"name": "Наименование актива (предприятия) проекта Сырьё."},
    "rm_raw_materials": {"name": "Наименование сырьевой позиции."},
    "rm_delivery_bases": {"name": "Наименование базиса поставки (например, FOB, FCA, CPT)."},
    "rm_raw_material_prices": {
        "fact_2025": "Цены → Фактическая цена за 2025 год; исходный показатель fact_2025.",
        "business_plan_quantity_tons": "Бизнес-план → Количество сырья, т; редактируется только в Ценах.",
        "business_plan_usd_per_ton": "Бизнес-план → Цена сырья, долл./т.",
        "business_plan_rub_per_ton": "Бизнес-план → Цена сырья, руб./т.",
        "delivery_cost_rub": "Стоимость доставки, руб.; исходный показатель delivery_cost_rub.",
        "business_plan_updated_at": "Время последнего изменения показателей бизнес-плана.",
        "business_plan_updated_by": "Автор последнего изменения показателей бизнес-плана.",
        "storage_standard_updated_at": "Время последнего изменения норматива запаса.",
        "storage_standard_updated_by": "Автор последнего изменения норматива запаса.",
    },
    "rm_report_snapshots": {
        "fixed_at": "Дата и время фиксации версии Движения.",
        "fixed_by_user_id": "Пользователь, зафиксировавший версию → app_users.id.",
        "fixed_by_email": "Адрес электронной почты автора фиксации на момент фиксации.",
        "fixed_by_fullname": "ФИО автора фиксации на момент фиксации.",
        "is_final": "Признак окончательной неизменяемой версии; false — предварительная версия.",
        "previous_snapshot_id": "Идентификатор перезаписанной предыдущей версии; не внешний ключ.",
    },
    "rm_snapshot_material_movements": {
        "snapshot_id": "Идентификатор зафиксированной версии → rm_report_snapshots.id.",
        "source_asset_raw_material_id": "UUID исходной связи актив–сырьё на момент фиксации; не внешний ключ.",
        "source_row_id": "UUID исходной строки Движения на момент фиксации; не внешний ключ.",
        "source_asset_id": "UUID исходного актива на момент фиксации; не внешний ключ.",
        "source_raw_material_id": "UUID исходной сырьевой позиции на момент фиксации; не внешний ключ.",
        "asset_name": "Наименование актива на момент фиксации.",
        "raw_material_name": "Наименование сырьевой позиции на момент фиксации.",
    },
    "kppk_orgs": {
        "num": "Числовой идентификатор ДЗО в raw.kppk_d_org.id; не прикладной UUID.",
        "code": "Код ДЗО из исходной системы.",
        "name": "Наименование ДЗО.",
    },
    "kppk_org_products": {
        "has_excise": "Заполняемый акциз этой позиции: у этого продукта на этом ДЗО есть акциз. Включает акциз в форме «Реализация»",
    },
    "kppk_markets": {
        "code": "Короткий код рынка сбыта, как в исходной таблице: РФ, ДЗ, БЗ.",
        "name": "Полное наименование рынка сбыта.",
    },
    "kppk_realization": {
        "product_id": "Идентификатор продукта → kppk_products.id; заменил текстовое имя из источника.",
        "market_id": "Идентификатор рынка сбыта → kppk_markets.id; заменил текстовый код региона из источника.",
    },
    "kppk_realization_plan": {
        "product_id": "Идентификатор продукта → kppk_products.id; заменил текстовое имя из источника.",
        "market_id": "Идентификатор рынка сбыта → kppk_markets.id; заменил текстовый код региона из источника.",
    },
    "kppk_cost": {
        "product_id": "Идентификатор продукта → kppk_products.id; заменил текстовое имя из источника.",
    },
    "kppk_production_plan": {
        "product_id": "Идентификатор продукта → kppk_products.id; заменил текстовое имя из источника.",
    },
    "kppk_products": {
        "product_num": "Уникальный номер продуктовой позиции; NULL для ненумерованных дополнений из матрицы.",
        "name": "Наименование продукта; префикс @ обозначает отсутствующее в исходном справочнике дополнение из матрицы.",
    },
    "kppk_raws": {"raw_num": "Уникальный номер сырьевой позиции.", "name": "Наименование сырьевой позиции КППК."},
    "collab_cell_states": {
        "key": "Канонический ключ ячейки: таблица/UUID строки/поле.",
        "state": "JSON: ревизия, последний пользователь, идентификатор действия и время изменения ячейки.",
    },
    "collab_edit_stacks": {
        "user_id": "UUID пользователя в текстовом виде.",
        "session_id": "Идентификатор сессии страницы; новая загрузка страницы начинает отдельную историю.",
        "scope": "Область истории: секция/отчётный месяц.",
        "actions": "JSON-массив действий с изменёнными ячейками, прежними/новыми значениями и состоянием отмены.",
        "expires_at": "Время истечения истории сессии после периода неактивности.",
    },
    "collab_outbox": {
        "id": "Последовательный числовой идентификатор события.",
        "room": "Ключ комнаты получателей события.",
        "payload": "JSON-сообщение протокола совместного редактирования, ожидающее публикации.",
    },
}

KPPK_SOURCE_METRICS = {
    "buyout_fact": "Факт → Выкуп → Объем.",
    "buyout_bp_plan": "Бизнес-план → Выкуп → Объем.",
    "buyout_max_plan": "Оперплан → Выкуп → Объем.",
    "month_plan": "План → Объем за месяц; исходное поле month_plan.",
    "month_part_plan": "План → Частичный объём за месяц; исходное поле month_part_plan.",
    "month_bp_plan": "Бизнес-план → Объем за месяц.",
    "month_max_plan": "Оперплан → Реализация → Объем за месяц; исходное поле month_max_plan.",
    "month_opt_plan": "Исходный показатель объёма month_opt_plan; не отображается в текущей форме КППК.",
    "fact": "Факт → Реализация → Объем.",
    "fca_price_plan": "План → Цена FCA; исходное поле fca_price_plan.",
    "fca_price_fact": "Факт → Выкуп → Цена FCA.",
    "fca_price_bp_plan": "Бизнес-план → Выкуп → Цена FCA.",
    "fca_price_max_plan": "Оперплан → Выкуп → Цена FCA.",
    "fca_price_opt_plan": "Исходный показатель цены FCA fca_price_opt_plan; не отображается в текущей форме КППК.",
    "fca_price_market_plan": "План → Цена FCA по рынку сбыта; исходное поле fca_price_market_plan.",
    "fca_price_market_fact": "Факт → Реализация → Цена FCA по рынку сбыта.",
    "fca_price_market_bp_plan": "Бизнес-план → Реализация → Цена FCA по рынку сбыта.",
    "fca_price_market_max_plan": "Оперплан → Реализация → Цена FCA по рынку сбыта.",
    "fca_price_market_opt_plan": "Исходный показатель цены FCA по рынку сбыта fca_price_market_opt_plan; не отображается в текущей форме КППК.",
    "var_exp_plan": "План → Переменные затраты; исходное поле var_exp_plan.",
    "var_exp_fact": "Факт → Переменные затраты; исходное поле var_exp_fact.",
    "var_exp_bp_plan": "Бизнес-план → Переменные затраты; исходное поле var_exp_bp_plan.",
    "export_duty_proc": "Экспортная пошлина, %; исходное поле export_duty_proc.",
    "export_duty_fact": "Факт → Экспортная пошлина; исходное поле export_duty_fact.",
    "export_duty_plan": "План → Экспортная пошлина; исходное поле export_duty_plan.",
    "export_duty_bp": "Бизнес-план → Экспортная пошлина; исходное поле export_duty_bp.",
    "export_duty_max_plan": "Оперплан → Экспортная пошлина; исходное поле export_duty_max_plan.",
    "export_duty_opt_plan": "Исходный показатель экспортной пошлины export_duty_opt_plan; не отображается в текущей форме КППК.",
    "excise_plan": "План → Акциз; исходное поле excise_plan.",
    "excise_fact": "Факт → Акциз; исходное поле excise_fact.",
    "excise_bp": "Бизнес-план → Акциз; исходное поле excise_bp.",
    "excise_max_plan": "Оперплан → Акциз; исходное поле excise_max_plan.",
    "excise_opt_plan": "Исходный показатель акциза excise_opt_plan; не отображается в текущей форме КППК.",
    "full_cost_plan": "План → Полная себестоимость; исходное поле full_cost_plan.",
    "full_cost_fact": "Факт → Полная себестоимость; исходное поле full_cost_fact.",
    "full_cost_bp": "Бизнес-план → Полная себестоимость; исходное поле full_cost_bp.",
    "input_cost_plan": "План → Производственная себестоимость; исходное поле input_cost_plan.",
    "input_cost_fact": "Факт → Производственная себестоимость; исходное поле input_cost_fact.",
    "input_cost_bp": "Бизнес-план → Производственная себестоимость; исходное поле input_cost_bp.",
    "stock": "Остаток продукции; исходное поле stock.",
    "stock_current": "Текущий остаток продукции; исходное поле stock_current.",
    "plan_to_sell": "Планируемый объём реализации; исходное поле plan_to_sell.",
    "goods": "Исходный показатель goods таблицы raw.kppk_f_production_plan; не отображается в текущей форме КППК.",
    "goods_bp_plan": "Исходный показатель goods_bp_plan таблицы raw.kppk_f_production_plan; не отображается в текущей форме КППК.",
    "stock_region": "Остаток продукции по региону; исходное поле stock_region.",
    "stock_current_region": "Текущий остаток продукции по региону; исходное поле stock_current_region.",
    "stock_plant": "Остаток продукции на заводе; исходное поле stock_plant.",
    "stock_current_plant": "Текущий остаток продукции на заводе; исходное поле stock_current_plant.",
}


def apply_table_comments(table: Table) -> None:
    """Attach descriptions when each model module registers its own tables."""
    table.comment = TABLE_COMMENTS[table.name].rstrip(".")
    for name, comment in column_comments_for(table).items():
        table.c[name].comment = comment


def column_comments_for(table: Table) -> dict[str, str | None]:
    """Build complete comments, refusing undocumented columns instead of guessing."""
    comments = dict(COMMON_COLUMNS)
    if table.name.startswith("rm_"):
        comments.update(MOVEMENT_COLUMNS)
    if "asset_id" in table.c:
        target = "r_assets" if table.name.startswith("r_") else "rm_assets"
        comments["asset_id"] = f"Идентификатор предприятия (актива) → {target}.id."
    comments.update(SPECIFIC_COLUMNS.get(table.name, {}))
    if table.name in {"kppk_realization", "kppk_realization_plan", "kppk_production_plan"}:
        comments.update(KPPK_SOURCE_METRICS)
    if table.name == "kppk_cost":
        for suffix, slice_name in (("bp", "Бизнес-план"), ("op", "Оперплан"), ("fact", "Факт")):
            for prefix, metric in (
                ("full_cost", "Полная"),
                ("production_cost", "Производственная"),
                ("variable_cost", "Переменная"),
            ):
                comments[f"{prefix}_{suffix}"] = f"{slice_name} → {metric} себестоимость."
    if table.name == "kppk_realization_plan":
        comments["month_bp_plan"] = "Бизнес-план → Реализация → Объем за месяц."
    if table.name == "r_plan_rows":
        from src.renovation.form_schema import field_header_paths  # noqa: PLC0415

        comments.update(field_header_paths())
    missing = set(table.c.keys()) - comments.keys()
    if missing:
        message = f"Missing data-catalog descriptions: {table.name}: {sorted(missing)}"
        raise ValueError(message)
    return {
        column.name: (
            None
            if column.name in {"id", "created_at", "updated_at"}
            else comments[column.name].replace("Идентификатор", "ID").replace("идентификатор", "ID").rstrip(".")
        )
        for column in table.columns
    }
