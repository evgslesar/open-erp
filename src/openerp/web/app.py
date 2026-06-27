from __future__ import annotations

import inspect
import io
import json
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from markupsafe import Markup
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from openerp.bootstrap import init_engine
from openerp.config import get_settings
from openerp.core.context import RequestContext
from openerp.core.import_export import (
    catalog_template_rows,
    export_rows_csv,
    export_rows_xlsx,
    import_catalog_rows,
    read_csv_rows,
    read_xlsx_rows,
)
from openerp.core.metadata import CatalogDef, DocumentDef, FieldDef, FieldType, TablePartDef
from openerp.core.posting import (
    ClosedPeriodError,
    DocumentPostingService,
    InsufficientFundsError,
    InsufficientStockError,
    InvalidPostingError,
    get_closed_period,
    set_closed_period,
)
from openerp.core.registers import RegisterService
from openerp.core.repository import DocumentStateError, Repository
from openerp.core.search import global_search
from urllib.parse import urlencode

from openerp.core.audit import list_audit_log, list_operation_log
from openerp.core.security import (
    AuthenticationError,
    PermissionDenied,
    authenticate,
    load_user_context,
)
from openerp.db import transaction
from openerp.modules.trade.reports import dashboard_summary, format_money_minor

templates = Jinja2Templates(directory="src/openerp/web/templates")
templates.env.filters["money"] = format_money_minor
templates.env.filters["tojson"] = lambda value: Markup(
    json.dumps(value, ensure_ascii=False)
)


def catalog_by_name(registry, catalog_name: str) -> CatalogDef:
    return next(item for item in registry.catalogs() if item.name == catalog_name)


def normalize_catalog_form(catalog: CatalogDef, form: dict[str, str]) -> dict:
    fields: list[FieldDef] = [FieldDef("name", FieldType.STRING, "Наименование")]
    fields.extend(catalog.fields)
    values = {}
    for field in fields:
        raw_value = form.get(field.name)
        if raw_value is None:
            continue
        if field.type in (FieldType.INTEGER, FieldType.MONEY):
            values[field.name] = int(raw_value or 0)
        elif field.type == FieldType.BOOLEAN:
            values[field.name] = raw_value in ("1", "true", "on")
        else:
            values[field.name] = raw_value
    return values


def coerce_field(field: FieldDef, raw: str | None) -> any:
    if raw is None or raw == "":
        return None
    if field.type in (FieldType.INTEGER, FieldType.MONEY):
        return int(raw)
    if field.type == FieldType.DECIMAL:
        return str(raw)
    if field.type == FieldType.DATE:
        return date.fromisoformat(raw)
    if field.type == FieldType.BOOLEAN:
        return raw in ("1", "true", "on")
    return raw


def parse_document_form(
    document_def: DocumentDef,
    form: dict[str, str],
) -> tuple[dict, dict[str, list[dict]]]:
    header: dict = {}
    table_parts: dict[str, list[dict]] = {part.name: [] for part in document_def.table_parts}

    for key, value in form.items():
        segments = key.split(".")
        if len(segments) == 3 and segments[0] in table_parts:
            part_name, index_str, field_name = segments
            try:
                index = int(index_str)
            except ValueError:
                continue
            bucket = table_parts[part_name]
            while len(bucket) <= index:
                bucket.append({})
            bucket[index][field_name] = value
        else:
            header[key] = value

    normalized_header: dict = {}
    for field in document_def.fields:
        coerced = coerce_field(field, header.get(field.name))
        if coerced is not None:
            normalized_header[field.name] = coerced
    if header.get("date"):
        normalized_header["date"] = date.fromisoformat(header["date"])
    if header.get("number"):
        normalized_header["number"] = header["number"]
    if header.get("comment"):
        normalized_header["comment"] = header["comment"]

    for part in document_def.table_parts:
        cleaned: list[dict] = []
        for row in table_parts[part.name]:
            if not any(row.values()):
                continue
            normalized: dict = {}
            for field in part.fields:
                coerced = coerce_field(field, row.get(field.name))
                if coerced is not None:
                    normalized[field.name] = coerced
            if normalized:
                cleaned.append(normalized)
        table_parts[part.name] = cleaned

    return normalized_header, table_parts


def catalog_name_for_field(field_name: str, registry) -> str | None:
    if not field_name.endswith("_id"):
        return None
    catalog_names = {catalog.name for catalog in registry.catalogs()}
    direct = field_name[:-3]
    if direct in catalog_names:
        return direct
    parts = direct.split("_", 1)
    if len(parts) == 2 and parts[1] in catalog_names:
        return parts[1]
    return None


def reference_options(repository: Repository, registry, field_name: str) -> list[dict] | None:
    catalog_name = catalog_name_for_field(field_name, registry)
    if catalog_name is None:
        return None
    try:
        return repository.list_catalog_items(catalog_name)
    except Exception:
        return None


def safe_redirect_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    return next_path


def document_filter_params(
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    counterparty_id: int | None = None,
    warehouse_id: int | None = None,
) -> dict:
    params: dict = {}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    if status:
        params["status"] = status
    if counterparty_id is not None:
        params["counterparty_id"] = counterparty_id
    if warehouse_id is not None:
        params["warehouse_id"] = warehouse_id
    return params


def document_filter_query(**params) -> str:
    cleaned = {key: value for key, value in params.items() if value not in (None, "")}
    return urlencode(cleaned)


def parse_based_on(value: str | None) -> tuple[str, int] | None:
    if not value or "/" not in value:
        return None
    doc_type, doc_id_str = value.split("/", 1)
    try:
        return doc_type, int(doc_id_str)
    except ValueError:
        return None


def serialize_row(row: dict) -> dict:
    result = {}
    for key, value in row.items():
        if isinstance(value, date):
            result[key] = value.isoformat()
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def table_part_rows_for_form(document: dict, part: TablePartDef) -> list[dict]:
    field_names = {field.name for field in part.fields}
    rows: list[dict] = []
    for row in document.get(part.name) or []:
        cleaned = {name: row[name] for name in field_names if name in row and row[name] is not None}
        rows.append(cleaned)
    return rows


def table_parts_for_form(document_def: DocumentDef, document: dict) -> dict[str, list[dict]]:
    return {
        part.name: table_part_rows_for_form(document, part) for part in document_def.table_parts
    }


def _collect_reference_options(
    repository: Repository,
    registry,
    document_def: DocumentDef,
) -> dict[str, list[dict]]:
    field_names = {field.name for field in document_def.fields if field.name.endswith("_id")}
    for part in document_def.table_parts:
        field_names.update(
            field.name for field in part.fields if field.name.endswith("_id")
        )
    options: dict[str, list[dict]] = {}
    for field_name in field_names:
        items = reference_options(repository, registry, field_name)
        if items is not None:
            options[field_name] = items
    return options


async def current_context(request: Request) -> RequestContext:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise AuthenticationError("Not authenticated")
    engine = request.app.state.engine
    with transaction(engine) as connection:
        return load_user_context(connection, int(user_id))


CurrentContext = Annotated[RequestContext, Depends(current_context)]


def _call_report_handler(handler, connection, registry, context, **params):
    accepted = {
        name
        for name, param in inspect.signature(handler).parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    has_varkw = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in inspect.signature(handler).parameters.values()
    )
    if has_varkw:
        filtered = params
    else:
        filtered = {k: v for k, v in params.items() if k in accepted}
    return handler(connection, registry, context, **filtered)


def create_app() -> FastAPI:
    settings = get_settings()
    engine, registry = init_engine()
    app = FastAPI(title="Open ERP")
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
    app.state.engine = engine
    app.state.registry = registry

    templates.env.globals["catalogs"] = registry.catalogs()
    templates.env.globals["documents"] = registry.documents()
    templates.env.globals["reports"] = [
        report for module in registry.modules.values() for report in module.reports
    ]
    app.state.registry = registry

    @app.exception_handler(AuthenticationError)
    async def on_auth_error(request: Request, _exc: AuthenticationError):
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(f"/login?next={next_path}", status_code=303)

    @app.exception_handler(DocumentStateError)
    async def on_document_state_error(request: Request, exc: DocumentStateError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=409,
        )

    @app.exception_handler(InsufficientStockError)
    async def on_insufficient_stock(request: Request, exc: InsufficientStockError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=409,
        )

    @app.exception_handler(InsufficientFundsError)
    async def on_insufficient_funds(request: Request, exc: InsufficientFundsError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=409,
        )

    @app.exception_handler(InvalidPostingError)
    async def on_invalid_posting(request: Request, exc: InvalidPostingError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=409,
        )

    @app.exception_handler(ClosedPeriodError)
    async def on_closed_period(request: Request, exc: ClosedPeriodError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=409,
        )

    @app.exception_handler(PermissionDenied)
    async def on_permission_denied(request: Request, exc: PermissionDenied):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "context": None},
            status_code=403,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, context: CurrentContext):
        reports = [
            report
            for module in registry.modules.values()
            for report in module.reports
        ]
        with transaction(engine) as connection:
            dashboard = dashboard_summary(connection, registry, context)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "catalogs": registry.catalogs(),
                "documents": registry.documents(),
                "reports": reports,
                "dashboard": dashboard,
                "context": context,
            },
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": None},
        )

    @app.post("/login")
    async def login(request: Request, next: str = "/"):
        form = dict(await request.form())
        email = form.get("email", "").strip()
        password = form.get("password", "")
        try:
            with transaction(engine) as connection:
                user = authenticate(connection, email, password)
        except AuthenticationError:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": next, "error": "Неверный email или пароль"},
                status_code=401,
            )
        request.session["user_id"] = user["id"]
        return RedirectResponse(safe_redirect_path(next), status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/catalogs/{catalog_name}", response_class=HTMLResponse)
    def catalog_list(
        request: Request,
        catalog_name: str,
        context: CurrentContext,
        q: str = "",
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            rows = repository.list_catalog_items(catalog_name, q=q or None)
        catalog = catalog_by_name(registry, catalog_name)
        return templates.TemplateResponse(
            request,
            "catalogs/list.html",
            {"catalog": catalog, "rows": rows, "q": q, "context": context},
        )

    @app.get("/catalogs/{catalog_name}/new", response_class=HTMLResponse)
    def catalog_new(
        request: Request,
        catalog_name: str,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        return templates.TemplateResponse(
            request,
            "catalogs/form.html",
            {
                "catalog": catalog,
                "item": {},
                "action": f"/catalogs/{catalog_name}/new",
                "context": context,
            },
        )

    @app.post("/catalogs/{catalog_name}/new")
    async def catalog_create(
        request: Request,
        catalog_name: str,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        form = dict(await request.form())
        values = normalize_catalog_form(catalog, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.create_catalog_item(catalog_name, values)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.get("/catalogs/{catalog_name}/{item_id}/edit", response_class=HTMLResponse)
    def catalog_edit(
        request: Request,
        catalog_name: str,
        item_id: int,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            item = repository.get_catalog_item(catalog_name, item_id)
        return templates.TemplateResponse(
            request,
            "catalogs/form.html",
            {
                "catalog": catalog,
                "item": item,
                "action": f"/catalogs/{catalog_name}/{item_id}/edit",
                "context": context,
            },
        )

    @app.post("/catalogs/{catalog_name}/{item_id}/edit")
    async def catalog_update(
        request: Request,
        catalog_name: str,
        item_id: int,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        form = dict(await request.form())
        values = normalize_catalog_form(catalog, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.update_catalog_item(catalog_name, item_id, values)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.post("/catalogs/{catalog_name}/{item_id}/delete")
    def catalog_delete(
        catalog_name: str,
        item_id: int,
        context: CurrentContext,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.delete_catalog_item(catalog_name, item_id)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.get("/catalogs/{catalog_name}/import", response_class=HTMLResponse)
    def catalog_import_form(
        request: Request,
        catalog_name: str,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        return templates.TemplateResponse(
            request,
            "catalogs/import.html",
            {"catalog": catalog, "result": None, "context": context},
        )

    @app.post("/catalogs/{catalog_name}/import")
    async def catalog_import(
        request: Request,
        catalog_name: str,
        context: CurrentContext,
    ):
        catalog = catalog_by_name(registry, catalog_name)
        form = await request.form()
        upload = form.get("file")
        dry_run = form.get("dry_run") == "1"
        if upload is None or not hasattr(upload, "read"):
            return templates.TemplateResponse(
                request,
                "catalogs/import.html",
                {
                    "catalog": catalog,
                    "result": {"errors": [{"row": 0, "error": "No file selected"}]},
                    "context": context,
                },
                status_code=400,
            )
        content = await upload.read()
        filename = getattr(upload, "filename", "") or ""
        if filename.endswith(".xlsx"):
            rows = read_xlsx_rows(content)
        else:
            rows = read_csv_rows(content)
        with transaction(engine) as connection:
            result = import_catalog_rows(
                connection, registry, context, catalog_name, rows, dry_run=dry_run
            )
        return templates.TemplateResponse(
            request,
            "catalogs/import.html",
            {"catalog": catalog, "result": result, "context": context},
        )

    @app.get("/catalogs/{catalog_name}/import-template")
    def catalog_import_template(
        catalog_name: str,
        context: CurrentContext,
    ):
        rows = catalog_template_rows(registry, catalog_name)
        buffer = io.BytesIO()
        export_rows_csv(rows, buffer)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{catalog_name}_template.csv"'
            },
        )

    @app.get("/documents/{document_name}", response_class=HTMLResponse)
    def document_list(
        request: Request,
        document_name: str,
        context: CurrentContext,
        after_date: date | None = None,
        after_id: int | None = None,
        limit: int = 50,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        counterparty_id: int | None = None,
        warehouse_id: int | None = None,
    ):
        filters = document_filter_params(
            date_from, date_to, status, counterparty_id, warehouse_id
        )
        filter_query = document_filter_query(**filters)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            rows = repository.list_documents_keyset(
                document_name,
                limit=limit,
                after_date=after_date,
                after_id=after_id,
                date_from=date_from,
                date_to=date_to,
                status=status or None,
                counterparty_id=counterparty_id,
                warehouse_id=warehouse_id,
            )
            filter_options: dict[str, list[dict]] = {}
            document_def = registry.document(document_name)
            field_names = {field.name for field in document_def.fields}
            if "counterparty_id" in field_names:
                filter_options["counterparty_id"] = reference_options(
                    repository, registry, "counterparty_id"
                ) or []
            if "warehouse_id" in field_names:
                filter_options["warehouse_id"] = reference_options(
                    repository, registry, "warehouse_id"
                ) or []
        document = registry.document(document_name)
        next_cursor = rows[-1] if len(rows) == limit else None
        is_hx = request.headers.get("HX-Request") == "true"
        template = "documents/_rows.html" if is_hx else "documents/list.html"
        return templates.TemplateResponse(
            request,
            template,
            {
                "document": document,
                "rows": rows,
                "next_cursor": next_cursor,
                "filters": filters,
                "filter_options": filter_options,
                "filter_query": filter_query,
                "context": context,
            },
        )

    @app.get("/documents/{document_name}/new", response_class=HTMLResponse)
    def document_new(
        request: Request,
        document_name: str,
        context: CurrentContext,
        based_on: str | None = None,
    ):
        document_def = registry.document(document_name)
        prefilled: dict = {}
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            based = parse_based_on(based_on)
            if based and document_name == "receipt":
                source_type, source_id = based
                if source_type == "purchase_order":
                    source = repository.get_document(source_type, source_id)
                    prefilled = {
                        "counterparty_id": source.get("counterparty_id"),
                        "warehouse_id": source.get("warehouse_id"),
                        "price_type_id": source.get("price_type_id"),
                        "based_on_document_id": source_id,
                        "lines": source.get("lines", []),
                    }
            references = _collect_reference_options(repository, registry, document_def)
        return templates.TemplateResponse(
            request,
            "documents/form.html",
            {
                "document_def": document_def,
                "document": prefilled,
                "form_table_rows": table_parts_for_form(document_def, prefilled),
                "action": f"/documents/{document_name}/new",
                "references": references,
                "context": context,
            },
        )

    @app.post("/documents/{document_name}/new")
    async def document_create(
        request: Request,
        document_name: str,
        context: CurrentContext,
    ):
        document_def = registry.document(document_name)
        form = dict(await request.form())
        values, table_parts = parse_document_form(document_def, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            document_id = repository.create_document(document_name, values, table_parts)
        return RedirectResponse(
            f"/documents/{document_name}/{document_id}", status_code=303
        )

    @app.get("/documents/{document_name}/{document_id}/edit", response_class=HTMLResponse)
    def document_edit(
        request: Request,
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        document_def = registry.document(document_name)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            document = repository.get_document(document_name, document_id)
            references = _collect_reference_options(repository, registry, document_def)
        return templates.TemplateResponse(
            request,
            "documents/form.html",
            {
                "document_def": document_def,
                "document": document,
                "form_table_rows": table_parts_for_form(document_def, document),
                "action": f"/documents/{document_name}/{document_id}/edit",
                "references": references,
                "context": context,
            },
        )

    @app.post("/documents/{document_name}/{document_id}/edit")
    async def document_update(
        request: Request,
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        document_def = registry.document(document_name)
        form = dict(await request.form())
        values, table_parts = parse_document_form(document_def, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.update_document(document_name, document_id, values, table_parts)
        return RedirectResponse(
            f"/documents/{document_name}/{document_id}", status_code=303
        )

    @app.post("/documents/{document_name}/{document_id}/delete")
    def document_delete(
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.delete_document(document_name, document_id)
        return RedirectResponse(f"/documents/{document_name}", status_code=303)

    @app.post("/documents/{document_name}/{document_id}/post")
    def document_post(
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        document_def = registry.document(document_name)
        if document_def.posting_handler is None:
            raise DocumentStateError(f"Document {document_name} cannot be posted")
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.get_document(document_name, document_id)
            DocumentPostingService(connection, registry, context).post(
                document_name, document_id
            )
        return RedirectResponse(
            f"/documents/{document_name}/{document_id}", status_code=303
        )

    @app.post("/documents/{document_name}/{document_id}/unpost")
    def document_unpost(
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            repository.get_document(document_name, document_id)
            DocumentPostingService(connection, registry, context).unpost(
                document_name, document_id
            )
        return RedirectResponse(
            f"/documents/{document_name}/{document_id}", status_code=303
        )

    @app.get("/documents/{document_name}/{document_id}", response_class=HTMLResponse)
    def document_view(
        request: Request,
        document_name: str,
        document_id: int,
        context: CurrentContext,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            document = repository.get_document(document_name, document_id)
        return templates.TemplateResponse(
            request,
            "documents/view.html",
            {
                "document_def": registry.document(document_name),
                "document": document,
                "context": context,
            },
        )

    @app.get("/documents/{document_name}/{document_id}/print", response_class=HTMLResponse)
    def document_print(
        request: Request,
        document_name: str,
        document_id: int,
        context: CurrentContext,
        form: str | None = None,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            document = repository.get_document(document_name, document_id)
        template_name = "documents/print_form.html"
        if form:
            for module in registry.modules.values():
                for print_form in module.print_forms:
                    if print_form.name == form and print_form.document == document_name:
                        template_name = print_form.template
                        break
        return templates.TemplateResponse(
            request,
            template_name,
            {"document_name": document_name, "document": document, "context": context},
        )

    @app.get("/reports/{report_name}", response_class=HTMLResponse)
    def report_view(
        request: Request,
        report_name: str,
        context: CurrentContext,
        on_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        report_def = registry.report(report_name)
        handler = registry.report_handlers[report_def.handler]
        params: dict = {}
        if on_date:
            params["on_date"] = on_date
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        with transaction(engine) as connection:
            rows = _call_report_handler(handler, connection, registry, context, **params)
        return templates.TemplateResponse(
            request,
            "reports/generic.html",
            {
                "report": report_def,
                "rows": rows,
                "on_date": on_date or date.today(),
                "date_from": date_from,
                "date_to": date_to or date.today(),
                "context": context,
            },
        )

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, context: CurrentContext, q: str = ""):
        q = q.strip()
        if not q:
            return RedirectResponse("/", status_code=303)
        with transaction(engine) as connection:
            data = global_search(connection, registry, context, q)
        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "query": data["query"],
                "groups": data["groups"],
                "total": data["total"],
                "context": context,
            },
        )

    @app.get("/api/search")
    def api_search(context: CurrentContext, q: str = "", limit: int = 10):
        limit = min(max(limit, 1), 50)
        with transaction(engine) as connection:
            data = global_search(connection, registry, context, q, limit=limit)
        flat = []
        for group in data["groups"]:
            for item in group["results"]:
                flat.append({
                    "type": item["type"],
                    "id": item["id"],
                    "group_key": group["group_key"],
                    "title": item["title"],
                    "subtitle": item.get("subtitle", ""),
                    "group_label": group["group_label"],
                    "group_url": group["group_url"],
                    "url": item["url"],
                })
        return JSONResponse({"query": data["query"], "results": flat, "total": data["total"]})

    @app.get("/settings/closed-period", response_class=HTMLResponse)
    def closed_period_form(request: Request, context: CurrentContext):
        if not context.is_admin:
            raise PermissionDenied("Only admins can manage closed period")
        with transaction(engine) as connection:
            closed_until = get_closed_period(connection, context)
        return templates.TemplateResponse(
            request,
            "settings/closed_period.html",
            {"closed_until": closed_until, "context": context},
        )

    @app.post("/settings/closed-period")
    async def closed_period_save(request: Request, context: CurrentContext):
        if not context.is_admin:
            raise PermissionDenied("Only admins can manage closed period")
        form = dict(await request.form())
        closed_until = date.fromisoformat(form["closed_until"])
        with transaction(engine) as connection:
            set_closed_period(connection, context, closed_until)
        return RedirectResponse("/settings/closed-period", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_list(
        request: Request,
        context: CurrentContext,
        date_from: date | None = None,
        date_to: date | None = None,
        operation: str = "",
    ):
        if not context.is_admin:
            raise PermissionDenied("Only admins can view audit log")
        with transaction(engine) as connection:
            rows = list_audit_log(
                connection,
                context,
                date_from=date_from,
                date_to=date_to,
                operation=operation or None,
            )
        return templates.TemplateResponse(
            request,
            "audit/list.html",
            {
                "rows": rows,
                "date_from": date_from,
                "date_to": date_to,
                "operation": operation,
                "context": context,
            },
        )

    @app.get("/operations", response_class=HTMLResponse)
    def operations_list(
        request: Request,
        context: CurrentContext,
        date_from: date | None = None,
        date_to: date | None = None,
        operation: str = "",
    ):
        if not context.is_admin:
            raise PermissionDenied("Only admins can view operation log")
        with transaction(engine) as connection:
            rows = list_operation_log(
                connection,
                context,
                date_from=date_from,
                date_to=date_to,
                operation=operation or None,
            )
        return templates.TemplateResponse(
            request,
            "operations/list.html",
            {
                "rows": rows,
                "date_from": date_from,
                "date_to": date_to,
                "operation": operation,
                "context": context,
            },
        )

    @app.get("/api/price")
    def api_price(
        context: CurrentContext,
        product_id: int,
        price_type_id: int | None = None,
        on_date: date | None = None,
    ):
        with transaction(engine) as connection:
            registers = RegisterService(connection, registry, context)
            filters: dict = {"product_id": product_id}
            if price_type_id is not None:
                filters["price_type_id"] = price_type_id
            rows = registers.slice_last("prices", on_date or date.today(), filters=filters)
        if not rows:
            return JSONResponse({"price": None, "currency_id": None})
        row = rows[0]
        return JSONResponse({"price": row["price"], "currency_id": row["currency_id"]})

    @app.get("/api/v1/catalogs/{catalog_name}")
    def api_catalog_list(
        catalog_name: str,
        context: CurrentContext,
        q: str = "",
        limit: int = 100,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            rows = repository.list_catalog_items(catalog_name, limit=limit, q=q or None)
        return JSONResponse({"items": [serialize_row(row) for row in rows]})

    @app.get("/api/v1/catalogs/{catalog_name}/{item_id}")
    def api_catalog_get(catalog_name: str, item_id: int, context: CurrentContext):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            item = repository.get_catalog_item(catalog_name, item_id)
        return JSONResponse(serialize_row(item))

    @app.get("/api/v1/documents/{document_name}")
    def api_document_list(
        document_name: str,
        context: CurrentContext,
        limit: int = 50,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        counterparty_id: int | None = None,
        warehouse_id: int | None = None,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            rows = repository.list_documents_keyset(
                document_name,
                limit=limit,
                date_from=date_from,
                date_to=date_to,
                status=status,
                counterparty_id=counterparty_id,
                warehouse_id=warehouse_id,
            )
        return JSONResponse({"items": [serialize_row(row) for row in rows]})

    @app.get("/api/v1/documents/{document_name}/{document_id}")
    def api_document_get(document_name: str, document_id: int, context: CurrentContext):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context)
            document = repository.get_document(document_name, document_id)
        return JSONResponse(serialize_row(document))

    @app.get("/reports/{report_name}/export")
    def report_export(
        report_name: str,
        context: CurrentContext,
        fmt: str = "csv",
        on_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        report_def = registry.report(report_name)
        handler = registry.report_handlers[report_def.handler]
        params: dict = {}
        if on_date:
            params["on_date"] = on_date
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        with transaction(engine) as connection:
            rows = _call_report_handler(handler, connection, registry, context, **params)
        buffer = io.BytesIO()
        if fmt == "xlsx":
            export_rows_xlsx(rows, buffer)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            export_rows_csv(rows, buffer)
            media = "text/csv"
        filename = f"{report_name}.{fmt}"
        return Response(
            content=buffer.getvalue(),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app
