from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.metadata import CatalogDef, FieldDef, FieldType
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.reports import stock_balance_report

templates = Jinja2Templates(directory="src/openerp/web/templates")


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


def create_app() -> FastAPI:
    engine, registry = init_engine()
    app = FastAPI(title="Open ERP")
    app.state.engine = engine
    app.state.registry = registry

    def context() -> RequestContext:
        return RequestContext(user_id=1, organization_id=1, is_admin=True)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        reports = [
            report
            for module in registry.modules.values()
            for report in module.reports
        ]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "catalogs": registry.catalogs(),
                "documents": registry.documents(),
                "reports": reports,
            },
        )

    @app.get("/catalogs/{catalog_name}", response_class=HTMLResponse)
    def catalog_list(request: Request, catalog_name: str):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            rows = repository.list_catalog_items(catalog_name)
        catalog = catalog_by_name(registry, catalog_name)
        return templates.TemplateResponse(
            request,
            "catalogs/list.html",
            {"catalog": catalog, "rows": rows},
        )

    @app.get("/catalogs/{catalog_name}/new", response_class=HTMLResponse)
    def catalog_new(request: Request, catalog_name: str):
        catalog = catalog_by_name(registry, catalog_name)
        return templates.TemplateResponse(
            request,
            "catalogs/form.html",
            {"catalog": catalog, "item": {}, "action": f"/catalogs/{catalog_name}/new"},
        )

    @app.post("/catalogs/{catalog_name}/new")
    async def catalog_create(request: Request, catalog_name: str):
        catalog = catalog_by_name(registry, catalog_name)
        form = dict(await request.form())
        values = normalize_catalog_form(catalog, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            repository.create_catalog_item(catalog_name, values)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.get("/catalogs/{catalog_name}/{item_id}/edit", response_class=HTMLResponse)
    def catalog_edit(request: Request, catalog_name: str, item_id: int):
        catalog = catalog_by_name(registry, catalog_name)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            item = repository.get_catalog_item(catalog_name, item_id)
        return templates.TemplateResponse(
            request,
            "catalogs/form.html",
            {
                "catalog": catalog,
                "item": item,
                "action": f"/catalogs/{catalog_name}/{item_id}/edit",
            },
        )

    @app.post("/catalogs/{catalog_name}/{item_id}/edit")
    async def catalog_update(request: Request, catalog_name: str, item_id: int):
        catalog = catalog_by_name(registry, catalog_name)
        form = dict(await request.form())
        values = normalize_catalog_form(catalog, form)
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            repository.update_catalog_item(catalog_name, item_id, values)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.post("/catalogs/{catalog_name}/{item_id}/delete")
    def catalog_delete(catalog_name: str, item_id: int):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            repository.delete_catalog_item(catalog_name, item_id)
        return RedirectResponse(f"/catalogs/{catalog_name}", status_code=303)

    @app.get("/documents/{document_name}", response_class=HTMLResponse)
    def document_list(
        request: Request,
        document_name: str,
        after_date: date | None = None,
        after_id: int | None = None,
    ):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            rows = repository.list_documents_keyset(
                document_name,
                after_date=after_date,
                after_id=after_id,
            )
        document = registry.document(document_name)
        next_cursor = rows[-1] if rows else None
        return templates.TemplateResponse(
            request,
            "documents/list.html",
            {"document": document, "rows": rows, "next_cursor": next_cursor},
        )

    @app.get("/documents/{document_name}/{document_id}", response_class=HTMLResponse)
    def document_view(request: Request, document_name: str, document_id: int):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            document = repository.get_document(document_name, document_id)
        return templates.TemplateResponse(
            request,
            "documents/view.html",
            {
                "document_def": registry.document(document_name),
                "document": document,
            },
        )

    @app.get("/documents/{document_name}/{document_id}/print", response_class=HTMLResponse)
    def document_print(request: Request, document_name: str, document_id: int):
        with transaction(engine) as connection:
            repository = Repository(connection, registry, context())
            document = repository.get_document(document_name, document_id)
        return templates.TemplateResponse(
            request,
            "documents/print_form.html",
            {"document_name": document_name, "document": document},
        )

    @app.get("/reports/stock-balance", response_class=HTMLResponse)
    def report_stock_balance(request: Request, on_date: date | None = None):
        with transaction(engine) as connection:
            rows = stock_balance_report(connection, registry, context(), on_date or date.today())
        return templates.TemplateResponse(
            request,
            "reports/stock_balance.html",
            {"rows": rows, "on_date": on_date or date.today()},
        )

    return app
