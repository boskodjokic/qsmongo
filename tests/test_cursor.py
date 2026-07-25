"""Keyset pagination.

The properties that matter: a page boundary never skips or repeats a document, a client cannot
forge a cursor, and paging cost does not grow with page number.
"""

from datetime import datetime

import pytest

from qsmongo import Cursors, Field, InvalidCursor, InvalidPagination, Schema, parse

SCHEMA = Schema(name=Field(str), score=Field(int), created_at=Field(datetime, alias="audit.created"))
SIGNED = Cursors(secret="s3cret")
UNSIGNED = Cursors()


def page_one(qs="sort=-score", **kwargs):
    return parse(qs, SCHEMA, cursors=SIGNED, **kwargs)


def test_after_is_rejected_unless_cursors_are_configured():
    with pytest.raises(InvalidCursor):
        parse("after=whatever", SCHEMA)


def test_sort_gains_an_id_tiebreaker_so_the_order_is_total():
    # Without this, two documents with the same score could straddle the boundary.
    assert page_one().sort == [("score", -1), ("_id", 1)]


def test_explicit_id_in_the_sort_is_not_duplicated():
    schema = Schema(score=Field(int), _id=Field(str))
    assert parse("sort=score,-_id", schema, cursors=SIGNED).sort == [("score", 1), ("_id", -1)]


def test_round_trip_produces_a_range_clause_not_a_skip():
    first = page_one()
    cursor = first.next_cursor({"score": 42, "_id": "abc"})

    second = parse(f"sort=-score&after={cursor}", SCHEMA, cursors=SIGNED)

    assert second.skip == 0
    assert second.filter == {
        "$or": [
            {"score": {"$lt": 42}},
            {"score": 42, "_id": {"$gt": "abc"}},
        ]
    }


def test_keyset_composes_with_the_rest_of_the_filter():
    first = page_one("name=Ada&sort=-score")
    cursor = first.next_cursor({"score": 42, "_id": "abc"})

    second = parse(f"name=Ada&sort=-score&after={cursor}", SCHEMA, cursors=SIGNED)

    assert second.filter["$and"][0] == {"name": "Ada"}
    assert "$or" in second.filter["$and"][1]


def test_ascending_sort_pages_forward_with_gt():
    first = parse("sort=score", SCHEMA, cursors=SIGNED)
    cursor = first.next_cursor({"score": 7, "_id": "abc"})
    second = parse(f"sort=score&after={cursor}", SCHEMA, cursors=SIGNED)
    assert second.filter["$or"][0] == {"score": {"$gt": 7}}


def test_datetimes_survive_the_round_trip_with_their_type():
    when = datetime(2026, 7, 25, 18, 30)
    first = parse("sort=-created_at", SCHEMA, cursors=SIGNED)
    cursor = first.next_cursor({"audit": {"created": when}, "_id": "abc"})

    second = parse(f"sort=-created_at&after={cursor}", SCHEMA, cursors=SIGNED)

    assert second.filter["$or"][0] == {"audit.created": {"$lt": when}}


def test_an_empty_page_has_no_next_cursor():
    assert page_one().next_cursor({}) is None
    assert page_one().next_cursor(None) is None


def test_a_tampered_cursor_is_rejected():
    cursor = page_one().next_cursor({"score": 42, "_id": "abc"})
    payload, _, signature = cursor.partition(".")
    forged = f"{payload}x.{signature}"
    with pytest.raises(InvalidCursor):
        parse(f"sort=-score&after={forged}", SCHEMA, cursors=SIGNED)


def test_an_unsigned_cursor_is_refused_when_signing_is_configured():
    unsigned = parse("sort=-score", SCHEMA, cursors=UNSIGNED).next_cursor({"score": 42, "_id": "abc"})
    with pytest.raises(InvalidCursor):
        parse(f"sort=-score&after={unsigned}", SCHEMA, cursors=SIGNED)


def test_garbage_is_rejected_rather_than_crashing():
    for junk in ("after=!!!!", "after=", "after=eyJhIjoxfQ"):
        with pytest.raises(InvalidCursor):
            parse(f"sort=-score&{junk}", SCHEMA, cursors=SIGNED)


def test_changing_the_sort_between_pages_is_an_error_not_silent_nonsense():
    cursor = parse("sort=-score,name", SCHEMA, cursors=SIGNED).next_cursor({"score": 42, "name": "Ada", "_id": "abc"})
    with pytest.raises(InvalidCursor):
        parse(f"sort=-score&after={cursor}", SCHEMA, cursors=SIGNED)


def test_a_different_sort_of_the_same_length_is_also_rejected():
    # The values alone are ambiguous: this used to decode happily and compare an int against a
    # str field. The cursor records which sort issued it.
    cursor = parse("sort=-score", SCHEMA, cursors=SIGNED).next_cursor({"score": 42, "_id": "abc"})
    with pytest.raises(InvalidCursor) as excinfo:
        parse(f"sort=name&after={cursor}", SCHEMA, cursors=SIGNED)
    assert "issued for sort" in str(excinfo.value)


def test_reversing_the_sort_direction_is_rejected():
    cursor = parse("sort=-score", SCHEMA, cursors=SIGNED).next_cursor({"score": 42, "_id": "abc"})
    with pytest.raises(InvalidCursor):
        parse(f"sort=score&after={cursor}", SCHEMA, cursors=SIGNED)


def test_after_and_page_cannot_be_combined():
    cursor = page_one().next_cursor({"score": 42, "_id": "abc"})
    with pytest.raises(InvalidPagination):
        parse(f"sort=-score&page=2&after={cursor}", SCHEMA, cursors=SIGNED)


def test_the_secret_never_reaches_a_repr():
    assert "s3cret" not in repr(SIGNED)
    assert "s3cret" not in repr(page_one())
