from typing import Any, List, Sequence

from tortoise import connections


def format_vector(embedding: Sequence[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in embedding) + "]"


def assert_vector_dimension(embedding: Sequence[float], *, expected: int, label: str) -> None:
    if len(embedding) != expected:
        raise ValueError(f"{label} dimension mismatch: {len(embedding)} != {expected}")


async def execute_query(query: str, values: List[Any] | None = None) -> tuple[int, list]:
    conn = connections.get("default")
    return await conn.execute_query(query, values or [])


def row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[index]
