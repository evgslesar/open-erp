# Open ERP

Open ERP — открытая веб-система учёта для малого бизнеса: торговая и складская подсистема, взаиморасчёты, денежные средства, цены и валюты, печатные формы, импорт/экспорт CSV/XLSX.

MVP покрывает:

- справочники: организации, контрагенты, номенклатура, склады, валюты, единицы, статьи ДДС, денежные счета, типы цен;
- документы: поступление, реализация, перемещение, корректировка остатков, заказ покупателя, кассовые и банковские платежи;
- регистры накопления (товары, взаиморасчёты, денежные средства) и регистры сведений (цены, курсы валют);
- сводка на главной (балансы, графики, таблицы);
- отчёты, печатные формы, импорт/экспорт CSV/XLSX;
- роли, разрешения, организации, аудит и журнал операций;
- SQLite по умолчанию, PostgreSQL для роста;
- расширения через доверенные Python-модули.

За рамками MVP: регламентированный бухучёт, зарплата, ОС, производство, налоговая отчётность, страновой комплаенс.

## Содержание

- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Быстрый старт для разработчика](#быстрый-старт-для-разработчика)
- [Конфигурация](#конфигурация)
- [База данных](#база-данных)
- [Метаданные и модули](#метаданные-и-модули)
- [Документы и проведение](#документы-и-проведение)
- [Регистры и отчёты](#регистры-и-отчёты)
- [Веб-слой (FastAPI)](#веб-слой-fastapi)
- [Импорт и экспорт](#импорт-и-экспорт)
- [Безопасность и сессии](#безопасность-и-сессии)
- [Аудит и журнал операций](#аудит-и-журнал-операций)
- [CLI](#cli)
- [Тестирование](#тестирование)
- [Стиль кода](#стиль-кода)
- [Расширение: новый модуль](#расширение-новый-модуль)
- [Лицензия](#лицензия)

## Архитектура

Система построена вокруг четырёх ключевых понятий: **метаданные**, **документы**, **регистры** и **идемпотентное проведение**. Все прикладные объекты описываются в Python-коде доверенных модулей. Платформа по этим описаниям создаёт таблицы, отрисовывает формы, проверяет права и предоставляет API отчётов.

```
+----------------------------+
|  Модули (metadata.py)      |
|  - catalogs, documents,    |
|    registers, reports      |
+-------------+--------------+
              |
              v
+-------------+--------------+      +-----------------------+
|  core/schema.py            |      |  core/posting.py      |
|  SQLAlchemy MetaData       | <--> |  DocumentPostingSvc   |
+-------------+--------------+      +-----------+-----------+
              |                                 |
              v                                 v
+-------------+--------------+      +-----------------------+
|  core/repository.py        |      |  core/registers.py    |
|  CRUD + ключи + аудит      |      |  RegisterService      |
+-------------+--------------+      +-----------+-----------+
              |                                 |
              +----------------+----------------+
                               |
                               v
                    +----------+-----------+
                    |  web/app.py (FastAPI)|
                    +----------+-----------+
                               |
                               v
                    +----------+-----------+
                    |  Jinja2 templates    |
                    +----------------------+
```

Ключевые решения:

- **Регистры накопления** хранят помесячные обороты. Остаток на дату = сумма всех прошлых месяцев + движения текущего месяца. Итоги пересчитываются инкрементально при каждом проведении.
- **Проведение** идемпотентно: повторное проведение удаляет старые движения и создаёт новые. Отмена проведения удаляет движения. Закрытые периоды блокируют изменения.
- **Аутентификация** через bcrypt + подписанные cookie-сессии (itsdangerous через Starlette SessionMiddleware).
- **Формы** генерируются из метаданных, динамические табличные части подключаются через Alpine.js.

## Структура проекта

```
src/openerp/
├── bootstrap.py              # сборка движка и реестра
├── config.py                 # настройки (env)
├── db.py                     # engine + transaction context
├── cli.py                    # команды Typer
├── core/
│   ├── audit.py              # аудит и журнал операций
│   ├── backup.py             # резервная копия SQLite
│   ├── context.py            # RequestContext
│   ├── decimal.py            # безопасные деньги/Decimal
│   ├── import_export.py      # CSV/XLSX импорт и экспорт
│   ├── metadata.py           # dataclass-описания + MetadataRegistry
│   ├── migrations.py         # ModuleMigrator
│   ├── naming.py             # правила именования таблиц
│   ├── posting.py            # DocumentPostingService
│   ├── registers.py          # RegisterService
│   ├── repository.py         # CRUD по справочникам/документам
│   ├── schema.py             # сборка SQLAlchemy MetaData
│   └── security.py           # bcrypt, require_permission
├── modules/trade/
│   ├── __init__.py           # register(registry)
│   ├── metadata.py           # описания модуля trade
│   ├── migrations.py         # миграции модуля trade
│   ├── posting.py            # обработчики проведения
│   ├── reports.py            # обработчики отчётов
│   └── demo.py               # сид демо-данных
└── web/
    ├── app.py                # FastAPI-приложение
    └── templates/            # Jinja2-шаблоны
tests/                         # pytest, TestClient
data/                          # SQLite и .secret_key (создаются при init)
backups/                       # вывод команды backup
docs/                          # пользовательская документация
```

Соглашение об именовании таблиц (`openerp/core/naming.py`):

| Объект               | Шаблон имени                       |
|----------------------|------------------------------------|
| Справочник           | `cat_<name>`                       |
| Документ             | `doc_<name>`                       |
| Табличная часть      | `doc_<document>_<part>`            |
| Движения регистра    | `reg_<name>_movements`             |
| Итоги регистра       | `reg_<name>_totals`                |
| Регистр сведений     | `ireg_<name>`                      |
| Системные таблицы    | `sys_*`                            |

## Быстрый старт для разработчика

Требования: Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\openerp init-db
.\.venv\Scripts\openerp seed-demo
.\.venv\Scripts\openerp run --port 8000
```

Откройте http://127.0.0.1:8000 и войдите как `admin@example.local` / `admin`. На главной странице отобразится сводка: денежные остатки, продажи за месяц, взаиморасчёты, графики и таблицы.

Если база уже была создана до появления учёта денег, повторно выполните `seed-demo` — команда дозаполнит демо-платежи, не дублируя товары.

Запуск в режиме автоперезагрузки для разработки:

```powershell
$env:OPENERP_DATABASE_URL = "sqlite:///data/dev.db"
.\.venv\Scripts\uvicorn openerp.web.app:create_app --factory --reload
```

## Конфигурация

Настройки — `openerp/config.py:Settings`:

| Переменная                | Назначение                                            | По умолчанию              |
|---------------------------|-------------------------------------------------------|---------------------------|
| `OPENERP_DATABASE_URL`    | URL БД (SQLAlchemy)                                   | `sqlite:///data/open_erp.db` |
| `OPENERP_SECRET_KEY`      | Ключ подписи cookie-сессий                            | авто-создаётся в `data/.secret_key` |

`data/.secret_key` создаётся при первом запуске (48 байт из `secrets.token_urlsafe`). Удаление файла инвалилирует все сессии. Права доступа к файлу выставляются в `0o600` (на ОС, где это поддерживается).

## База данных

`openerp/db.py`:

- `create_db_engine(url)` создаёт `sqlalchemy.create_engine(..., future=True)`;
- для SQLite включает PRAGMA: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`;
- `transaction(engine)` — контекстный менеджер `engine.begin()`.

Для PostgreSQL используйте `psycopg[binary]` (`pip install -e ".[postgres]"`) и переменную `OPENERP_DATABASE_URL=postgresql+psycopg://user:pass@host/db`.

Схема собирается из метаданных в `openerp/core/schema.py`:

- `system_tables` создаёт служебные таблицы `sys_organizations`, `sys_users`, `sys_roles`, `sys_permissions`, `sys_user_roles`, `sys_module_versions`, `sys_module_migrations`, `sys_audit_log`, `sys_operation_log`, `sys_closed_periods`, `sys_number_sequences`;
- `build_catalog`, `build_document`, `build_accumulation_register`, `build_information_register` материализуют прикладные таблицы;
- базовые колонки: `id`, `organization_id`, `created_at`, `created_by`, `updated_at`, `updated_by`, `deletion_mark`, `revision`.

Все прикладные записи имеют `organization_id` — это базовая мультиарендность по организации.

При запуске `init-db` / `init_engine` выполняется `ModuleMigrator`: обновляются версии модулей и применяются миграции (например, `trade/20260622_add_money_accounts` — справочник денежных счетов и поле `money_account_id` в платежах и регистре `cash`).

## Метаданные и модули

`openerp/core/metadata.py` определяет следующие dataclass'ы:

| Тип                 | Класс                         |
|---------------------|-------------------------------|
| Поле                | `FieldDef`                    |
| Табличная часть     | `TablePartDef`                |
| Справочник          | `CatalogDef`                  |
| Документ            | `DocumentDef`                 |
| Регистр накопления  | `AccumulationRegisterDef`     |
| Регистр сведений    | `InformationRegisterDef`      |
| Отчёт               | `ReportDef`                   |
| Печатная форма      | `PrintFormDef`                |
| Модуль              | `ModuleDef`                   |
| Реестр              | `MetadataRegistry`            |

`FieldType` (StrEnum): `STRING, TEXT, INTEGER, MONEY, DECIMAL, DATE, DATETIME, BOOLEAN`. Маппинг в SQL (`openerp/core/schema.py:sql_type`):

- `STRING` → `String(255)`;
- `TEXT` → `Text`;
- `INTEGER, MONEY` → `Integer`;
- `DECIMAL` → `String(64)` (хранится как строка для предсказуемой арифметики);
- `DATE, DATETIME` → `Date, DateTime`;
- `BOOLEAN` → `Boolean`.

Модуль `trade` (`openerp/modules/trade/metadata.py`) собирает каталоги, документы, регистры, отчёты и печатные формы модуля.

Подключение модуля:

```python
# openerp/bootstrap.py
def build_registry() -> MetadataRegistry:
    registry = MetadataRegistry()
    register_trade(registry)        # openerp.modules.trade.register
    return registry
```

Каждый модуль должен экспортировать `register(registry: MetadataRegistry)`, в которой:

1. вызвать `registry.register_module(ModuleDef(...))`;
2. зарегистрировать обработчики проведения: `registry.register_posting_handler("trade.post_receipt", post_receipt)`;
3. зарегистрировать обработчики отчётов: `registry.register_report_handler("trade.stock_balance_report", stock_balance_report)`.

## Документы и проведение

Жизненный цикл документа (`DocumentStatus`):

- `DRAFT` — черновик, можно редактировать и удалять;
- `POSTED` — проведён, движения зарегистрированы, редактирование запрещено;
- `CANCELLED` — отменён (отмена проведения), движения удалены;
- `DELETION_MARKED` — помечен на удаление (мягкое удаление).

`openerp/core/posting.py:DocumentPostingService.post` алгоритм:

1. `require_permission(... "post")`;
2. прочитать документ и проверить, что период не закрыт (`sys_closed_periods.closed_until`);
3. удалить ранее созданные движения документа через `RegisterService.delete_registrator_movements`;
4. вызвать обработчик проведения модуля, передав `PostingContext`;
5. для регистров без `allow_negative` вызвать `assert_no_negative_balances`;
6. перевести статус документа в `POSTED`.

`unpost` делает шаги 2–3 и переводит статус в `CANCELLED`.

`repost` — это `post` + запись в журнал операций.

Для склада и денежных средств отрицательные остатки запрещены. При проведении продажи система проверяет остаток товара на дату документа. При расходном кассовом или банковском платеже проверяется доступный остаток по денежному счету и валюте; если денег недостаточно, проведение завершается ошибкой `InsufficientFundsError`.

Пример обработчика проведения (`openerp/modules/trade/posting.py:post_receipt`):

```python
def post_receipt(context: PostingContext) -> None:
    registers = RegisterService(context.connection, context.registry, context.context)
    document = context.document
    for line in document["lines"]:
        registers.add_movement(
            "stock",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={"warehouse_id": document["warehouse_id"], "product_id": line["product_id"]},
            resources={"quantity": to_decimal(line["quantity"])},
        )
        registers.add_movement(
            "settlements",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={"counterparty_id": document["counterparty_id"], "currency_id": line["currency_id"]},
            resources={"amount_minor": -line["amount_minor"]},
        )
```

## Регистры и отчёты

`openerp/core/registers.py:RegisterService` инкапсулирует всю работу с регистрами:

- `add_movement(register_name, period, registrator_type, registrator_id, line_no, dimensions, resources)` — пишет в `reg_<name>_movements` и инкрементально обновляет `reg_<name>_totals` для месяца периода;
- `delete_registrator_movements(registrator_type, registrator_id)` — откатывает все движения документа;
- `balance(register_name, on_date, dimensions=None, filters=None)` — остаток на дату (итоги прошлых месяцев + движения текущего);
- `turnover(register_name, start_date, end_date, ...)` — обороты за период;
- `movements(register_name, start_date, end_date, ...)` — список движений;
- `slice_last(register_name, on_date, ...)` — срез последних значений регистра сведений;
- `balance_and_turnover(...)` — начальный остаток, оборот и конечный остаток;
- `rebuild_totals(register_name)` — полный пересчёт итогов из движений;
- `verify_totals(register_name)` — список расхождений между движениями и итогами;
- `assert_no_negative_balances(register_name, on_date)` — для регистров без `allow_negative`.

Месячный ключ итогов — `period_start = month_start(period)` (`openerp/core/registers.py:month_start`). Итог за прошлые месяцы + движения текущего = остаток на любую дату.

В модуле `trade` регистр `cash` хранит движения денежных средств по типу счета, денежному счету, статье ДДС и валюте. Контроль отрицательного остатка агрегирует деньги по `money_account_id` и `currency_id`, поэтому расход по одной статье ДДС может использовать общий доступный остаток счета. Отчёт `cash` также показывает итоговый остаток по денежному счету и валюте.

Отчёты (`openerp/modules/trade/reports.py`) — это функции `(connection, registry, context, **params) -> list[dict]`. Параметры приходят из query-string; веб-слой фильтрует их по сигнатуре (`web/app.py:_call_report_handler`), лишние параметры молча отбрасываются.

```python
def stock_balance_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    rows = service.balance("stock", on_date or date.today())
    ...
    return rows
```

Регистрация: `registry.register_report_handler("trade.stock_balance_report", stock_balance_report)`.

Сводка для главной страницы — `dashboard_summary(...)` в `openerp/modules/trade/reports.py`. Функция агрегирует остатки денег, продажи с начала месяца, взаиморасчёты, топ товаров и данные для графиков (Chart.js). Маршрут `/` передаёт результат в шаблон `index.html`.

## Веб-слой (FastAPI)

`openerp/web/app.py:create_app` — фабрика FastAPI:

- подключает `SessionMiddleware` с `secret_key` из настроек;
- регистрирует шаблоны `Jinja2Templates("src/openerp/web/templates")`;
- навешивает обработчики ошибок `AuthenticationError` (редирект на `/login`), `DocumentStateError`, `InsufficientStockError`, `InsufficientFundsError` и `InvalidPostingError` (409 + `error.html`).

Маршруты:

| Метод  | Путь                                              | Назначение                                            |
|--------|---------------------------------------------------|-------------------------------------------------------|
| GET    | `/`                                               | Главная: сводка (KPI, графики, таблицы) + навигация   |
| GET    | `/login`, POST `/login`, POST `/logout`           | Аутентификация                                        |
| GET    | `/catalogs/{name}`                                | Список элементов справочника                          |
| GET/POST | `/catalogs/{name}/new`                          | Создание элемента                                     |
| GET/POST | `/catalogs/{name}/{id}/edit`                   | Редактирование                                        |
| POST   | `/catalogs/{name}/{id}/delete`                    | Помечен на удаление                                   |
| GET    | `/catalogs/{name}/import`                         | Форма импорта                                         |
| POST   | `/catalogs/{name}/import`                         | Загрузка CSV/XLSX                                     |
| GET    | `/catalogs/{name}/import-template`                | Скачивание шаблона CSV                                |
| GET    | `/documents/{name}`                               | Список документов (keyset-пагинация)                  |
| GET/POST | `/documents/{name}/new`                        | Создание документа                                    |
| GET/POST | `/documents/{name}/{id}/edit`                  | Редактирование                                        |
| POST   | `/documents/{name}/{id}/delete`                   | Пометка удаления                                      |
| POST   | `/documents/{name}/{id}/post`                     | Провести документ                                     |
| POST   | `/documents/{name}/{id}/unpost`                   | Отменить проведение                                   |
| GET    | `/documents/{name}/{id}`                          | Просмотр документа                                    |
| GET    | `/documents/{name}/{id}/print`                    | Печатная форма                                        |
| GET    | `/reports/{name}?on_date=&date_from=&date_to=`    | HTML-отчёт                                            |
| GET    | `/reports/{name}/export?fmt=csv\|xlsx&...`        | Скачивание отчёта в CSV/XLSX                         |

Параметры отчётов: `on_date` (для отчётов по остаткам), `date_from` и `date_to` (для отчётов по оборотам). Для каждого отчёта фильтруются параметры по сигнатуре обработчика.

Зависимость `CurrentContext = Annotated[RequestContext, Depends(current_context)]` подтягивает пользователя из cookie-сессии и наполняет контекст `organization_id`, `is_admin`. Все эндпоинты, требующие авторизации, используют её.

Шаблоны Jinja2 (`src/openerp/web/templates/`):

- `base.html` — общая разметка;
- `login.html`, `error.html`, `index.html`;
- `catalogs/list.html`, `form.html`, `import.html`;
- `documents/list.html`, `form.html`, `view.html`, `print_form.html`;
- `reports/generic.html`.

## Импорт и экспорт

`openerp/core/import_export.py`:

- `read_csv_rows(source)` — читает CSV с BOM (`utf-8-sig`);
- `read_xlsx_rows(source)` — читает первый лист через `openpyxl` (`read_only=True, data_only=True`);
- `export_rows_csv(rows, target)` — `csv.DictWriter` (для пустого списка — пустой файл);
- `export_rows_xlsx(rows, target)` — `openpyxl.Workbook`, заголовки из ключей первой строки;
- `import_catalog_rows(...)` — создаёт элементы справочника, в ответе `{imported, errors, imported_ids}`;
- `import_initial_stock_rows(...)` — создаёт и проводит `inventory_adjustment` с `quantity_delta` (строки: `product_sku, warehouse_name, quantity`);
- `catalog_template_rows(registry, catalog_name)` — возвращает список из одной «пустой» строки-шаблона с дефолтами.

Импорт каталога поддерживает режим `dry_run` (чек, без записи).

CLI:

```powershell
.\.venv\Scripts\openerp import-catalog product .\products.csv --fmt csv
.\.venv\Scripts\openerp import-catalog product .\products.xlsx --fmt xlsx --dry-run
.\.venv\Scripts\openerp import-initial-stock .\initial.csv
.\.venv\Scripts\openerp catalog-template product .\product_template.csv
```

## Безопасность и сессии

`openerp/core/security.py`:

- `hash_password(password)` / `verify_password(password, hash)` — bcrypt;
- `authenticate(connection, email, password)` — возвращает строку пользователя или бросает `AuthenticationError`;
- `load_user_context(connection, user_id)` — собирает `RequestContext` (выставляет `is_admin`, если у пользователя есть роль `admin`);
- `require_permission(connection, context, object_name, operation)` — проверка `sys_permissions`. Админ проходит без проверки.

Имя объекта для разрешений:

- справочник: `catalog:<name>` (например, `catalog:product`);
- документ: `document:<name>` (например, `document:sale`);
- системное: `system:closed_period`.

Операции: `create, read, update, delete, post, unpost` (для системных — по контексту).

Cookie-сессии: `Starlette.SessionMiddleware` с `secret_key` из настроек и `same_site="lax"`. Ключ сессии — `user_id`.

## Аудит и журнал операций

`openerp/core/audit.py`:

- `log_audit(connection, context, object_type, operation, object_id, before, after)` — пишет `sys_audit_log` (полный снимок до/после, JSON);
- `log_operation(connection, context, operation, object_type, object_id, details)` — пишет `sys_operation_log` (краткое событие, JSON-детали).

`Repository.create_catalog_item`, `update_catalog_item`, `delete_catalog_item`, `create_document`, `update_document`, `delete_document` пишут аудит автоматически. Проведение/отмена/репост пишут операции.

## CLI

`openerp/cli.py` — приложение Typer. Все команды запускаются как `openerp <command>` (точка входа объявлена в `pyproject.toml`).

| Команда                                      | Описание                                                  |
|----------------------------------------------|-----------------------------------------------------------|
| `init-db`                                    | Создать схему БД                                          |
| `seed-demo`                                  | Загрузить демо-данные и администратора; повторный запуск дозаполняет денежные платежи, если их ещё нет |
| `run [--host 127.0.0.1] [--port 8000]`        | Запустить uvicorn                                         |
| `backup`                                     | Сделать резервную копию SQLite в `backups/`                |
| `set-password <email> <password>`            | Сменить пароль пользователя                               |
| `export-stock <file> [--fmt csv\|xlsx]`       | Выгрузка отчёта «Остатки товаров»                         |
| `import-catalog <catalog> <file> [--fmt ...] [--dry-run]` | Импорт справочника                                |
| `import-initial-stock <file> [--dry-run]`     | Начальные остатки через `inventory_adjustment`            |
| `catalog-template <catalog> <file>`          | Сохранить CSV-шаблон справочника                          |
| `rebuild-totals <register>`                  | Пересчитать итоги регистра накопления                     |
| `verify-totals <register>`                   | Проверить итоги регистра накопления                       |

## Тестирование

Тесты в `tests/` (pytest, `TestClient`). Запуск:

```powershell
.\.venv\Scripts\pytest -q
```

Покрытие:

- `test_auth.py` — аутентификация, редиректы, сессии;
- `test_posting.py` — проведение, отмена, пересчёт итогов, неотрицательные остатки товаров и денег;
- `test_documents_ui.py` — формы документов, добавление строк;
- `test_demo.py` — идемпотентный `seed-demo`, сводка `dashboard_summary`;
- `test_dashboard.py` — главная страница с KPI и графиками;
- `test_reports.py` — отчёты, экспорт, лишние query-параметры;
- `test_reports_ui_data.py` — данные отчётов и экспорт;
- `test_import.py` — импорт CSV/XLSX.

Тестовая БД — временный файл в `tmp_path` (см. `tests/conftest.py:app_state`).

## Стиль кода

`ruff` (`pyproject.toml`):

```powershell
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff format .
```

Правила: `E, F, I, UP, B`, `line-length = 100`, `target-version = "py311"`.

Соглашения:

- `from __future__ import annotations` в каждом модуле;
- `Decimal` для денег и количеств; `Integer` хранится в копейках/минутах;
- даты — `datetime.date`, время — `datetime.datetime` (UTC, naive);
- все эндпоинты FastAPI получают контекст через `CurrentContext`;
- ошибки домена — `AuthenticationError`, `PermissionDenied`, `DocumentStateError`, `ClosedPeriodError`, `InsufficientStockError`, `InsufficientFundsError`, `InvalidPostingError`.

## Расширение: новый модуль

Минимальный шаблон модуля `crm`:

```
src/openerp/modules/crm/
├── __init__.py        # register(registry)
├── metadata.py        # каталоги, документы, регистры
├── posting.py         # обработчики проведения
└── reports.py         # обработчики отчётов
```

```python
# openerp/modules/crm/metadata.py
from openerp.core.metadata import (
    CatalogDef, DocumentDef, ModuleDef, FieldDef, FieldType,
)

client = CatalogDef(
    name="client",
    label="Клиенты CRM",
    fields=(FieldDef("phone", FieldType.STRING, "Телефон", indexed=True),),
)

lead = DocumentDef(
    name="lead",
    label="Лид",
    fields=(FieldDef("client_id", FieldType.INTEGER, "Клиент", required=True, indexed=True),),
)

crm = ModuleDef(
    name="crm",
    version="0.1.0",
    catalogs=(client,),
    documents=(lead,),
)
```

```python
# openerp/modules/crm/__init__.py
from openerp.core.metadata import MetadataRegistry
from .metadata import crm
from . import posting, reports

def register(registry: MetadataRegistry) -> None:
    registry.register_module(crm)
    registry.register_posting_handler("crm.post_lead", posting.post_lead)
    registry.register_report_handler("crm.lead_report", reports.lead_report)
```

Подключите модуль в `openerp/bootstrap.py:build_registry`. Новые таблицы создаются автоматически при следующем `init-db`.

## Лицензия

AGPLv3. См. `LICENSE`.
