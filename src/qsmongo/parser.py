"""Query string -> :class:`~qsmongo.query.Query`.

Grammar::

    field=value              equality
    field__<op>=value        op in: ne gt gte lt lte in nin regex exists
    field__in=a,b,c          comma separated list, "\\," escapes a literal comma
    sort=-created_at,name    "-" prefix means descending
    page=2&per_page=50       1-based paging, clamped to max_per_page

Field names are matched against a :class:`~qsmongo.schema.Schema`; anything undeclared raises.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl

from .coerce import coerce
from .errors import InvalidPagination, InvalidValue, UnsupportedOperator
from .query import Query
from .schema import Schema

OP_SUFFIXES = {
    "ne": "$ne",
    "gt": "$gt",
    "gte": "$gte",
    "lt": "$lt",
    "lte": "$lte",
    "in": "$in",
    "nin": "$nin",
    "regex": "$regex",
    "exists": "$exists",
}
LIST_OPS = frozenset({"in", "nin"})
MAX_REGEX_LENGTH = 200


def _split_list(raw: str) -> list[str]:
    """Split on commas, honouring a backslash escape so values may contain a literal comma."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:  # trailing backslash is a literal backslash
        current.append("\\")
    parts.append("".join(current))
    return [p for p in parts if p != ""]


def _split_key(key: str) -> tuple[str, str]:
    """``"age__gte"`` -> ``("age", "gte")``. A field with no known suffix is an equality match."""
    if "__" in key:
        field, _, suffix = key.rpartition("__")
        if field and suffix in OP_SUFFIXES:
            return field, suffix
    return key, "eq"


def _positive_int(raw: str, param: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise InvalidPagination(f"{param}: expected a positive integer, got {raw!r}", param=param) from None
    if value < 1:
        raise InvalidPagination(f"{param}: must be 1 or greater, got {value}", param=param)
    return value


def _parse_sort(raw: str, schema: Schema) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        direction = 1
        if token.startswith("-"):
            direction = -1
            token = token[1:]
        field_def = schema.get(token)  # raises UnknownField
        if not field_def.sortable:
            raise UnsupportedOperator(f"field {token!r} is not sortable", param=token)
        order.append((schema.column(token), direction))
    return order


def _build_value(field_def, op: str, raw: str, param: str) -> Any:
    if op == "exists":
        return coerce(bool, raw, param)
    if op in LIST_OPS:
        values = [coerce(field_def.type, item, param) for item in _split_list(raw)]
        if not values:
            raise InvalidValue(f"{param}: expected at least one value", param=param)
        return values
    if op == "regex":
        if len(raw) > MAX_REGEX_LENGTH:
            raise InvalidValue(f"{param}: pattern exceeds {MAX_REGEX_LENGTH} characters", param=param)
        return raw
    return coerce(field_def.type, raw, param)


def parse(
    query_string: str,
    schema: Schema,
    *,
    default_per_page: int = 25,
    max_per_page: int = 100,
    page_param: str = "page",
    per_page_param: str = "per_page",
    sort_param: str = "sort",
    ignore_unknown: bool = False,
) -> Query:
    """Parse ``query_string`` into a :class:`Query` validated against ``schema``.

    Set ``ignore_unknown`` to skip undeclared parameters instead of raising — useful when the
    same URL also carries parameters your view consumes (``?include=...``, cache busters).
    """
    pairs = parse_qsl(query_string.lstrip("?"), keep_blank_values=True)

    equality: dict[str, tuple[str, list[Any]]] = {}
    operators: dict[str, dict[str, Any]] = {}
    sort: list[tuple[str, int]] = []
    page = 1
    per_page = default_per_page

    for raw_key, raw_value in pairs:
        key = raw_key.strip()
        if key == page_param:
            page = _positive_int(raw_value, page_param)
            continue
        if key == per_page_param:
            per_page = min(_positive_int(raw_value, per_page_param), max_per_page)
            continue
        if key == sort_param:
            sort.extend(_parse_sort(raw_value, schema))
            continue

        field_name, op = _split_key(key)
        if ignore_unknown and field_name not in schema:
            continue
        field_def = schema.get(field_name)  # raises UnknownField
        if op not in field_def.ops:
            raise UnsupportedOperator(
                f"field {field_name!r} does not support {op!r} (allowed: {sorted(field_def.ops)})", param=key
            )

        column = schema.column(field_name)
        value = _build_value(field_def, op, raw_value, key)
        if op == "eq":
            equality.setdefault(column, (field_name, []))[1].append(value)
        else:
            operators.setdefault(column, {})[OP_SUFFIXES[op]] = value

    filter_: dict[str, Any] = {}
    for column, (field_name, values) in equality.items():
        if len(values) == 1:
            filter_[column] = values[0]
        else:
            if not schema.get(field_name).multi:
                raise InvalidValue(
                    f"{field_name!r} was given {len(values)} values; declare it as Field(..., multi=True) "
                    "to combine them into an $in",
                    param=field_name,
                )
            filter_[column] = {"$in": values}

    for column, clauses in operators.items():
        if column in filter_:
            raise InvalidValue(
                f"{column!r} has both an equality match and {sorted(clauses)}; use one or the other", param=column
            )
        filter_[column] = clauses

    return Query(
        filter=filter_,
        sort=sort,
        skip=(page - 1) * per_page,
        limit=per_page,
        page=page,
        per_page=per_page,
    )
