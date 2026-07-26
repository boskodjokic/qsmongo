"""Index advice.

The rule under test is MongoDB's ESR ordering: Equality keys first, then Sort keys, then Range
keys. Getting that order wrong is the difference between a seek and a collection scan, and it is
invisible until production data arrives.
"""

from datetime import datetime

import pytest

from qsmongo import Cursors, Field, Index, Schema, analyze, parse

SCHEMA = Schema(
    tenant_id=Field(str),
    status=Field(str),
    price=Field(float),
    name=Field(str),
    pattern=Field(str, ops={"eq", "regex"}),
    created_at=Field(datetime, alias="audit.created"),
)


def advise(qs, indexes=(), **kwargs):
    return analyze(parse(qs, SCHEMA), list(indexes), **kwargs)


def test_esr_order_is_suggested_when_nothing_matches():
    advice = advise("status=live&price__gte=10&sort=-created_at")
    assert not advice.ok
    # Equality, then Sort, then Range.
    assert advice.suggestion == (("status", 1), ("audit.created", -1), ("price", 1))
    assert advice.suggestion_spec == {"status": 1, "audit.created": -1, "price": 1}


def test_an_index_in_esr_order_serves_the_query():
    index = Index([("status", 1), ("audit.created", -1), ("price", 1)], name="esr")
    advice = advise("status=live&price__gte=10&sort=-created_at", [index])
    assert advice.ok
    assert advice.index is index
    assert advice.warnings == ()


def test_a_range_before_the_sort_key_means_an_in_memory_sort():
    # The classic mistake: {status, price, created_at} looks reasonable but the range on price
    # stops the index from providing the ordering.
    index = Index([("status", 1), ("price", 1), ("audit.created", -1)], name="rse")
    advice = advise("status=live&price__gte=10&sort=-created_at", [index])
    assert not advice.ok
    assert "sorts in memory" in advice.warnings[0]
    assert advice.suggestion == (("status", 1), ("audit.created", -1), ("price", 1))


def test_a_fully_reversed_sort_still_uses_the_index():
    index = Index([("status", 1), ("audit.created", 1)])
    assert advise("status=live&sort=-created_at", [index]).ok


def test_sorting_on_a_field_pinned_by_equality_is_free():
    index = Index([("status", 1)])
    assert advise("status=live&sort=status", [index]).ok


def test_an_index_that_only_provides_the_sort_serves_a_query_with_no_predicates():
    # A "latest 25" listing filters on nothing; the index exists purely so the server walks the
    # order rather than collecting the whole collection and sorting it.
    index = Index([("audit.created", -1)])
    advice = advise("sort=-created_at", [index])

    assert advice.ok
    assert advice.index is index
    assert advice.warnings == ()


def test_an_index_leading_on_a_different_field_does_not_serve_a_sort_only_query():
    assert not advise("sort=-created_at", [Index([("status", 1), ("audit.created", -1)])]).ok


def test_a_sort_only_query_under_keyset_pagination_is_served_by_the_sort_index():
    cursors = Cursors(secret="k")
    query = parse("sort=-created_at", SCHEMA, cursors=cursors)

    advice = analyze(query, [Index([("_id", 1)], name="_id_"), Index([("audit.created", -1), ("_id", 1)], name="ok")])

    assert advice.ok
    assert advice.index.name == "ok"


def test_no_index_at_all_is_reported_as_a_collection_scan():
    advice = advise("status=live")
    assert not advice.ok
    assert "collection scan" in advice.warnings[0]


def test_a_query_with_no_predicates_needs_no_advice():
    assert analyze(parse("", SCHEMA), []).warnings == ()


def test_extra_equality_puts_the_tenant_at_the_front():
    # The tenant clause is added by application code, so the advisor cannot see it in the filter.
    advice = advise("status=live&sort=-created_at", extra_equality=["tenant_id"])
    assert advice.suggestion == (("tenant_id", 1), ("status", 1), ("audit.created", -1))


def test_extra_equality_makes_an_otherwise_unused_index_match():
    index = Index([("tenant_id", 1), ("status", 1), ("audit.created", -1)])
    assert advise("status=live&sort=-created_at", [index], extra_equality=["tenant_id"]).ok
    # Without declaring it, the same index looks like it starts on the wrong field.
    assert not advise("status=live&sort=-created_at", [index]).ok


def test_case_insensitive_substring_search_is_flagged_as_a_scan():
    advice = advise("name__contains=ada")
    assert any("cannot use an index" in warning for warning in advice.warnings)


def test_an_anchored_case_sensitive_prefix_is_treated_as_a_range():
    schema = Schema(code=Field(str, case_sensitive=True))
    advice = analyze(parse("code__startswith=AB", schema), [Index([("code", 1)])])
    assert advice.ok
    assert advice.warnings == ()


def test_negation_and_missing_field_predicates_are_flagged():
    assert any("$ne/$nin" in w for w in advise("status__ne=draft").warnings)
    assert any("$exists=false" in w for w in advise("status__exists=false").warnings)


def test_a_field_with_both_bounds_is_a_range_not_an_equality():
    advice = advise("price__gte=1&price__lte=9&sort=name")
    assert advice.suggestion == (("name", 1), ("price", 1))


def test_keyset_pagination_does_not_change_the_recommended_index():
    cursors = Cursors(secret="k")
    first = parse("status=live&sort=-created_at", SCHEMA, cursors=cursors)
    token = first.next_cursor({"audit": {"created": datetime(2026, 1, 1)}, "_id": "abc"})
    second = parse(f"status=live&sort=-created_at&after={token}", SCHEMA, cursors=cursors)

    index = Index([("status", 1), ("audit.created", -1), ("_id", 1)])
    assert analyze(second, [index]).ok


def test_covered_queries_are_reported():
    index = Index([("status", 1), ("name", 1)])
    assert advise("status=live&fields=name", [index]).covered
    assert not advise("status=live&fields=price", [index]).covered


def test_index_information_from_pymongo_is_accepted():
    information = {
        "_id_": {"key": [("_id", 1)], "v": 2},
        "status_1_created_-1": {"key": [("status", 1), ("audit.created", -1)], "v": 2},
        "loc_2dsphere": {"key": [("loc", "2dsphere")], "v": 2},  # non-integer keys are ignored
    }
    indexes = Index.from_index_information(information)
    assert [index.name for index in indexes] == ["_id_", "status_1_created_-1"]
    assert advise("status=live&sort=-created_at", indexes).ok


def test_report_is_readable():
    report = str(advise("status=live&price__gte=10&sort=-created_at"))
    assert "no index serves this query" in report
    assert "suggested index: {status: 1, audit.created: -1, price: 1}" in report


@pytest.mark.parametrize("qs", ["status=live", "price__gte=1", "sort=name", "name__contains=x"])
def test_advice_never_raises(qs):
    analyze(parse(qs, SCHEMA), [Index([("status", 1)])])
