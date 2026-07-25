"""End-to-end proof of the property keyset pagination exists for.

Offset pagination silently breaks when the sort key has ties: documents shift between pages as
the cursor moves and rows get skipped or served twice. This walks a dataset full of ties, page by
page, using only the filters qsmongo produces, and asserts the result is exactly the full ordering.

A tiny matcher stands in for MongoDB — it supports only the operators the keyset filter emits.
"""

from qsmongo import Cursors, Field, Schema, parse

SCHEMA = Schema(score=Field(int), _id=Field(str))
CURSORS = Cursors(secret="test-key")
PER_PAGE = 4

# 24 documents over 6 distinct scores: every page boundary lands in the middle of a tie.
DOCS = [{"_id": f"{i:02d}", "score": i % 6} for i in range(24)]


def matches(document: dict, condition: dict) -> bool:
    for key, expected in condition.items():
        if key == "$or":
            if not any(matches(document, branch) for branch in expected):
                return False
        elif key == "$and":
            if not all(matches(document, branch) for branch in expected):
                return False
        elif isinstance(expected, dict):
            actual = document[key]
            for operator, operand in expected.items():
                if operator == "$gt" and not actual > operand:
                    return False
                if operator == "$lt" and not actual < operand:
                    return False
        elif document[key] != expected:
            return False
    return True


def run(query) -> list[dict]:
    """Apply a Query to DOCS the way a database would."""
    found = [doc for doc in DOCS if matches(doc, query.filter)]
    for column, direction in reversed(query.sort):
        found.sort(key=lambda doc: doc[column], reverse=direction == -1)
    return found[: query.limit]


def walk(sort: str) -> list[str]:
    """Page through the whole dataset with cursors, collecting ids in order."""
    seen: list[str] = []
    query = parse(f"sort={sort}&per_page={PER_PAGE}", SCHEMA, cursors=CURSORS)
    for _ in range(len(DOCS) // PER_PAGE + 2):  # bounded so a bug cannot hang the suite
        page = run(query)
        if not page:
            break
        seen.extend(doc["_id"] for doc in page)
        cursor = query.next_cursor(page[-1])
        query = parse(f"sort={sort}&per_page={PER_PAGE}&after={cursor}", SCHEMA, cursors=CURSORS)
    return seen


def expected(sort: str) -> list[str]:
    descending = sort.startswith("-")
    ordered = sorted(DOCS, key=lambda doc: (-doc["score"] if descending else doc["score"], doc["_id"]))
    return [doc["_id"] for doc in ordered]


def test_descending_sort_with_ties_visits_every_document_exactly_once():
    assert walk("-score") == expected("-score")


def test_ascending_sort_with_ties_visits_every_document_exactly_once():
    assert walk("score") == expected("score")


def test_no_document_is_repeated():
    seen = walk("-score")
    assert len(seen) == len(set(seen)) == len(DOCS)


def test_paging_stops_cleanly_at_the_end():
    last = parse(f"sort=-score&per_page={PER_PAGE}", SCHEMA, cursors=CURSORS)
    for _ in range(len(DOCS) // PER_PAGE):
        page = run(last)
        last = parse(f"sort=-score&per_page={PER_PAGE}&after={last.next_cursor(page[-1])}", SCHEMA, cursors=CURSORS)
    assert run(last) == []
