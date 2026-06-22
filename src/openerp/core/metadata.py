from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldType(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    MONEY = "money"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"
    DELETION_MARKED = "deletion_marked"


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: FieldType
    label: str
    required: bool = False
    default: Any | None = None
    reference: str | None = None
    indexed: bool = False


@dataclass(frozen=True)
class TablePartDef:
    name: str
    label: str
    fields: tuple[FieldDef, ...]


@dataclass(frozen=True)
class CatalogDef:
    name: str
    label: str
    fields: tuple[FieldDef, ...]
    hierarchical: bool = False


@dataclass(frozen=True)
class DocumentDef:
    name: str
    label: str
    fields: tuple[FieldDef, ...]
    table_parts: tuple[TablePartDef, ...] = ()
    posting_handler: str | None = None


@dataclass(frozen=True)
class AccumulationRegisterDef:
    name: str
    label: str
    dimensions: tuple[FieldDef, ...]
    resources: tuple[FieldDef, ...]
    allow_negative: bool = False
    totals_period: str = "month"


@dataclass(frozen=True)
class InformationRegisterDef:
    name: str
    label: str
    dimensions: tuple[FieldDef, ...]
    resources: tuple[FieldDef, ...]


@dataclass(frozen=True)
class ReportDef:
    name: str
    label: str
    handler: str


@dataclass(frozen=True)
class PrintFormDef:
    name: str
    label: str
    document: str
    template: str
    pdf_enabled: bool = False


@dataclass(frozen=True)
class ModuleDef:
    name: str
    version: str
    catalogs: tuple[CatalogDef, ...] = ()
    documents: tuple[DocumentDef, ...] = ()
    accumulation_registers: tuple[AccumulationRegisterDef, ...] = ()
    information_registers: tuple[InformationRegisterDef, ...] = ()
    reports: tuple[ReportDef, ...] = ()
    print_forms: tuple[PrintFormDef, ...] = ()


PostingCallable = Callable[[Any], None]
ReportCallable = Callable[..., Any]


@dataclass
class MetadataRegistry:
    modules: dict[str, ModuleDef] = field(default_factory=dict)
    posting_handlers: dict[str, PostingCallable] = field(default_factory=dict)
    report_handlers: dict[str, ReportCallable] = field(default_factory=dict)

    def register_module(self, module: ModuleDef) -> None:
        if module.name in self.modules:
            raise ValueError(f"Module already registered: {module.name}")
        self.modules[module.name] = module

    def register_posting_handler(self, name: str, handler: PostingCallable) -> None:
        self.posting_handlers[name] = handler

    def register_report_handler(self, name: str, handler: ReportCallable) -> None:
        self.report_handlers[name] = handler

    def catalogs(self) -> list[CatalogDef]:
        return [item for module in self.modules.values() for item in module.catalogs]

    def documents(self) -> list[DocumentDef]:
        return [item for module in self.modules.values() for item in module.documents]

    def accumulation_registers(self) -> list[AccumulationRegisterDef]:
        return [
            item
            for module in self.modules.values()
            for item in module.accumulation_registers
        ]

    def information_registers(self) -> list[InformationRegisterDef]:
        return [
            item
            for module in self.modules.values()
            for item in module.information_registers
        ]

    def document(self, name: str) -> DocumentDef:
        for document in self.documents():
            if document.name == name:
                return document
        raise KeyError(name)

    def accumulation_register(self, name: str) -> AccumulationRegisterDef:
        for register in self.accumulation_registers():
            if register.name == name:
                return register
        raise KeyError(name)
