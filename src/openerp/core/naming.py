from __future__ import annotations


def catalog_table(name: str) -> str:
    return f"cat_{name}"


def document_table(name: str) -> str:
    return f"doc_{name}"


def table_part_table(document_name: str, table_part_name: str) -> str:
    return f"doc_{document_name}_{table_part_name}"


def register_movements_table(name: str) -> str:
    return f"reg_{name}_movements"


def register_totals_table(name: str) -> str:
    return f"reg_{name}_totals"


def information_register_table(name: str) -> str:
    return f"ireg_{name}"
