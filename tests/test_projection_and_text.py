"""Projection and the escaped substring operators."""

import pytest

from qsmongo import Field, InvalidProjection, InvalidValue, Schema, UnknownField, UnsupportedOperator, parse

SCHEMA = Schema(
    name=Field(str),
    code=Field(str, case_sensitive=True),
    price=Field(float),
    internal_cost=Field(float, projectable=False),
    description=Field(str, ops=set(), sortable=False),  # selectable, never queryable
    pattern=Field(str, ops={"eq", "regex"}),
)


def test_fields_becomes_an_inclusion_projection():
    assert parse("fields=name,price", SCHEMA).projection == {"name": 1, "price": 1}


def test_minus_prefix_becomes_an_exclusion_projection():
    assert parse("fields=-description", SCHEMA).projection == {"description": 0}


def test_mixing_inclusion_and_exclusion_is_rejected_before_mongo_sees_it():
    with pytest.raises(InvalidProjection):
        parse("fields=name,-description", SCHEMA)


def test_unprojectable_fields_cannot_be_selected():
    with pytest.raises(UnsupportedOperator):
        parse("fields=internal_cost", SCHEMA)


def test_unknown_fields_cannot_be_selected():
    with pytest.raises(UnknownField):
        parse("fields=password_hash", SCHEMA)


def test_no_projection_means_none_rather_than_an_empty_dict():
    assert parse("name=Ada", SCHEMA).projection is None


def test_projection_reaches_find_kwargs_and_the_pipeline():
    query = parse("fields=name", SCHEMA)
    assert query.find_kwargs()["projection"] == {"name": 1}
    assert query.pipeline()[-1] == {"$project": {"name": 1}}


def test_a_field_with_no_ops_is_selectable_but_not_queryable():
    assert parse("fields=description", SCHEMA).projection == {"description": 1}
    with pytest.raises(UnsupportedOperator):
        parse("description=anything", SCHEMA)


def test_contains_escapes_the_user_value():
    # ".*" is a literal here, not "match everything".
    assert parse("name__contains=.*", SCHEMA).filter == {"name": {"$regex": r"\.\*", "$options": "i"}}


def test_startswith_and_endswith_anchor_the_pattern():
    assert parse("name__startswith=Ad", SCHEMA).filter == {"name": {"$regex": "^Ad", "$options": "i"}}
    assert parse("name__endswith=ce", SCHEMA).filter == {"name": {"$regex": "ce$", "$options": "i"}}


def test_case_sensitive_fields_drop_the_i_option_so_an_index_can_be_used():
    assert parse("code__startswith=AB", SCHEMA).filter == {"code": {"$regex": "^AB"}}


def test_raw_regex_is_opt_in_per_field():
    assert parse("pattern__regex=^a.c$", SCHEMA).filter == {"pattern": {"$regex": "^a.c$"}}
    with pytest.raises(UnsupportedOperator):
        parse("name__regex=^a.c$", SCHEMA)


def test_text_operators_require_a_string_field():
    with pytest.raises(UnsupportedOperator):
        parse("price__contains=9", SCHEMA)
    with pytest.raises(ValueError):
        Schema(n=Field(int, ops={"contains"}))


def test_empty_substring_is_rejected_rather_than_matching_everything():
    with pytest.raises(InvalidValue):
        parse("name__contains=", SCHEMA)


def test_two_clauses_on_the_same_operator_are_reported():
    with pytest.raises(InvalidValue):
        parse("name__contains=a&name__startswith=b", SCHEMA)


def test_range_clauses_on_one_field_still_merge():
    assert parse("price__gte=1&price__lte=9", SCHEMA).filter == {"price": {"$gte": 1.0, "$lte": 9.0}}
