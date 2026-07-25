"""Index advice for a parsed query.

This is a **lint, not a query planner**. It applies MongoDB's ESR guideline — an efficient compound
index lists Equality fields first, then Sort fields, then Range fields — to the query qsmongo just
built, and tells you whether one of your declared indexes can serve it. `explain()` remains the
only ground truth; this is here to catch the obvious problems before they reach a database.

    from qsmongo import Index, analyze, parse

    query = parse(request.url.query, schema)
    advice = analyze(query, INDEXES, extra_equality=["tenant_id"])
    if not advice.ok:
        log.warning("unindexed query: %s", advice)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .query import Query

RANGE_OPERATORS = frozenset({"$gt", "$gte", "$lt", "$lte"})


@dataclass(frozen=True, init=False)
class Index:
    """A compound index, in the same key order MongoDB stores it."""

    keys: tuple[tuple[str, int], ...]
    name: str | None = None

    def __init__(self, keys, name: str | None = None):
        object.__setattr__(self, "keys", tuple((column, int(direction)) for column, direction in keys))
        object.__setattr__(self, "name", name)

    @classmethod
    def from_index_information(cls, information: dict[str, Any]) -> list[Index]:
        """Build the index list straight from ``collection.index_information()``."""
        indexes = []
        for name, spec in information.items():
            keys = [(column, direction) for column, direction in spec["key"] if isinstance(direction, int)]
            if keys:
                indexes.append(cls(keys, name=name))
        return indexes

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(column for column, _ in self.keys)

    def __str__(self) -> str:
        body = ", ".join(f"{column}: {direction}" for column, direction in self.keys)
        return f"{{{body}}}" + (f" ({self.name})" if self.name else "")


@dataclass(frozen=True)
class IndexAdvice:
    """What the lint concluded."""

    ok: bool
    index: Index | None = None
    covered: bool = False
    warnings: tuple[str, ...] = ()
    suggestion: tuple[tuple[str, int], ...] = ()

    @property
    def suggestion_spec(self) -> dict[str, int]:
        """The suggested index in ``create_index`` form."""
        return dict(self.suggestion)

    def __str__(self) -> str:
        if self.ok:
            head = f"served by {self.index}" + (" (covered)" if self.covered else "")
        else:
            head = "no index serves this query"
        lines = [head, *(f"  - {warning}" for warning in self.warnings)]
        if not self.ok and self.suggestion:
            lines.append(f"  suggested index: {Index(self.suggestion)}")
        return "\n".join(lines)


def _classify(filter_: dict[str, Any], equality: dict[str, None], ranges: dict[str, None], warnings: list[str]):
    """Sort every predicate into equality, range, or 'cannot use an index'."""
    for key, value in filter_.items():
        if key == "$and":
            for branch in value:
                _classify(branch, equality, ranges, warnings)
        elif key == "$or":
            # The keyset clause lands here. Its fields are the sort keys, and it reads as a seek
            # into the index, so treat them as ranges rather than warning about an index union.
            for branch in value:
                seen: dict[str, None] = {}
                _classify(branch, seen, seen, warnings)
                for column in seen:
                    ranges.setdefault(column, None)
        elif isinstance(value, dict):
            operators = set(value)
            if "$in" in operators:
                equality.setdefault(key, None)
            if operators & RANGE_OPERATORS:
                ranges.setdefault(key, None)
            if "$regex" in operators:
                anchored = str(value["$regex"]).startswith("^") and "i" not in str(value.get("$options", ""))
                if anchored:
                    ranges.setdefault(key, None)
                else:
                    warnings.append(
                        f"{key}: an unanchored or case-insensitive regex cannot use an index and scans "
                        "every candidate document"
                    )
            if "$ne" in operators or "$nin" in operators:
                warnings.append(f"{key}: $ne/$nin match almost everything, so an index barely narrows the scan")
            if value.get("$exists") is False:
                warnings.append(f"{key}: $exists=false cannot use a standard index")
        else:
            equality.setdefault(key, None)


def _match(index: Index, equality: dict[str, None], sort: list[tuple[str, int]], ranges: dict[str, None]):
    """Walk the index keys ESR-style. Returns (prefix_length, sort_is_indexed)."""
    keys = index.keys
    position = 0
    while position < len(keys) and keys[position][0] in equality:
        position += 1

    # A sort on a field pinned by an equality predicate is free — every matching document shares
    # that value — so it does not need to appear in the index after the prefix.
    effective = [entry for entry in sort if entry[0] not in equality]
    if not effective:
        return position, True

    window = keys[position : position + len(effective)]
    if len(window) < len(effective):
        return position, False
    same = all(w == e for w, e in zip(window, effective, strict=True))
    # Walking an index backwards is free, but only if every key is reversed.
    reversed_ = all(w[0] == e[0] and w[1] == -e[1] for w, e in zip(window, effective, strict=True))
    return position, same or reversed_


def analyze(
    query: Query,
    indexes: list[Index] | None = None,
    *,
    extra_equality: list[str] = (),
) -> IndexAdvice:
    """Check ``query`` against ``indexes`` and suggest one when nothing fits.

    Args:
        indexes: your declared indexes, or ``Index.from_index_information(collection.index_information())``.
        extra_equality: columns your own code pins outside the parsed filter — a tenant id, a
            soft-delete flag. They belong at the front of the index, so the advice is wrong
            without them.
    """
    equality: dict[str, None] = {column: None for column in extra_equality}
    ranges: dict[str, None] = {}
    warnings: list[str] = []
    _classify(query.filter, equality, ranges, warnings)
    for column in ranges:  # a field with both bounds is a range, not an equality
        equality.pop(column, None)

    sort = list(query.sort)
    effective_sort = [entry for entry in sort if entry[0] not in equality]
    suggestion = tuple(
        [(column, 1) for column in equality]
        + effective_sort
        + [(column, 1) for column in ranges if column not in dict(effective_sort)]
    )

    best: tuple[int, bool] = (0, False)
    best_index: Index | None = None
    for index in indexes or []:
        prefix, sort_ok = _match(index, equality, sort, ranges)
        usable = prefix > 0 or (index.keys and index.keys[0][0] in ranges)
        if not usable:
            continue
        if (sort_ok, prefix) > (best[1], best[0]):
            best, best_index = (prefix, sort_ok), index

    ok = best_index is not None and best[1]
    if best_index is not None and not best[1]:
        warnings.append(
            f"{best_index} filters this query but does not provide the sort {Index(effective_sort)}, "
            "so MongoDB sorts in memory (it aborts past its blocking-sort memory limit, 100 MB by default)"
        )
    elif best_index is None and (equality or ranges or effective_sort):
        warnings.append("no index matches the leading field of this query, so it is a collection scan")

    covered = False
    if ok and query.projection and all(direction == 1 for direction in query.projection.values()):
        wanted = set(query.projection) - {"_id"}
        covered = wanted.issubset(set(best_index.columns))

    return IndexAdvice(
        ok=ok,
        index=best_index,
        covered=covered,
        warnings=tuple(warnings),
        suggestion=() if ok else suggestion,
    )
