from datetime import datetime

import pytest

from qsmongo import Field, InvalidPagination, Schema, UnknownField, UnsupportedOperator, parse

SCHEMA = Schema(
    name=Field(str),
    created_at=Field(datetime, alias="audit.created"),
    note=Field(str, sortable=False),
)


def test_default_page_window():
    query = parse("", SCHEMA)
    assert (query.page, query.per_page, query.skip, query.limit) == (1, 25, 0, 25)


def test_skip_follows_from_page():
    query = parse("page=3&per_page=10", SCHEMA)
    assert (query.skip, query.limit) == (20, 10)


def test_per_page_is_clamped_so_a_client_cannot_ask_for_everything():
    assert parse("per_page=100000", SCHEMA, max_per_page=100).per_page == 100


def test_page_must_be_a_positive_integer():
    for bad in ("page=0", "page=-1", "page=two"):
        with pytest.raises(InvalidPagination):
            parse(bad, SCHEMA)


def test_sort_direction_and_alias():
    assert parse("sort=-created_at", SCHEMA).sort == [("audit.created", -1)]


def test_sort_accepts_several_fields_in_order():
    assert parse("sort=name,-created_at", SCHEMA).sort == [("name", 1), ("audit.created", -1)]


def test_sort_rejects_unknown_and_unsortable_fields():
    with pytest.raises(UnknownField):
        parse("sort=password", SCHEMA)
    with pytest.raises(UnsupportedOperator):
        parse("sort=note", SCHEMA)


def test_find_kwargs_are_ready_for_pymongo():
    query = parse("name=Ada&sort=name&page=2&per_page=5", SCHEMA)
    assert query.find_kwargs() == {"filter": {"name": "Ada"}, "skip": 5, "limit": 5, "sort": [("name", 1)]}


def test_pipeline_mirrors_find():
    query = parse("name=Ada&sort=name&page=2&per_page=5", SCHEMA)
    assert query.pipeline() == [
        {"$match": {"name": "Ada"}},
        {"$sort": {"name": 1}},
        {"$skip": 5},
        {"$limit": 5},
    ]


def test_datetime_values_are_parsed_including_trailing_z():
    query = parse("created_at__gte=2026-01-01T00:00:00Z", SCHEMA)
    assert query.filter["audit.created"]["$gte"].year == 2026


def test_reserved_parameter_names_can_be_renamed():
    schema = Schema(page=Field(int))
    query = parse("page=7", schema, page_param="_page")
    assert query.filter == {"page": 7}
    assert query.page == 1
