"""FilterBuilder — the pipeline without the URL grammar.

parse() is one front-end onto this. Anything that can name a field, an operator and a raw value
gets the same whitelist, coercion and conflict checks.
"""

import pytest

from qsmongo import Field, FilterBuilder, InvalidValue, Schema, UnknownField, UnsupportedOperator, parse

SCHEMA = Schema(
    name=Field(str),
    age=Field(int),
    email=Field(str, alias="contact.email"),
    tag=Field(str, multi=True),
    secret=Field(str, ops={"eq"}),
)


def test_a_single_predicate():
    assert FilterBuilder(SCHEMA).add("name", "eq", "Ada").build() == {"name": "Ada"}


def test_eq_is_the_default_operator():
    assert FilterBuilder(SCHEMA).add("name", value="Ada").build() == {"name": "Ada"}


def test_calls_chain():
    filter_ = FilterBuilder(SCHEMA).add("age", "gte", "21").add("name", "contains", "ada").build()
    assert filter_ == {"age": {"$gte": 21}, "name": {"$regex": "ada", "$options": "i"}}


def test_values_are_coerced_from_strings_exactly_as_in_a_url():
    assert FilterBuilder(SCHEMA).add("age", "gte", "21").build() == {"age": {"$gte": 21}}


def test_range_bounds_merge_on_one_field():
    filter_ = FilterBuilder(SCHEMA).add("age", "gte", "21").add("age", "lt", "65").build()
    assert filter_ == {"age": {"$gte": 21, "$lt": 65}}


def test_aliases_apply():
    assert FilterBuilder(SCHEMA).add("email", value="a@b.co").build() == {"contact.email": "a@b.co"}


def test_multi_collapses_repeated_values_to_in():
    builder = FilterBuilder(SCHEMA).add("tag", value="red").add("tag", value="blue")
    assert builder.build() == {"tag": {"$in": ["red", "blue"]}}


def test_the_whitelist_still_applies():
    with pytest.raises(UnknownField):
        FilterBuilder(SCHEMA).add("password", value="x")


def test_per_field_operator_limits_still_apply():
    with pytest.raises(UnsupportedOperator):
        FilterBuilder(SCHEMA).add("secret", "contains", "x")


def test_conflicting_clauses_are_still_caught():
    with pytest.raises(InvalidValue):
        FilterBuilder(SCHEMA).add("age", value="21").add("age", "gte", "30").build()
    with pytest.raises(InvalidValue):
        FilterBuilder(SCHEMA).add("name", "contains", "a").add("name", "startswith", "b")


def test_errors_name_the_caller_supplied_parameter():
    with pytest.raises(UnknownField) as excinfo:
        FilterBuilder(SCHEMA).add("nope", value="x", param="filter[nope]")
    assert excinfo.value.param == "filter[nope]"


def test_default_param_matches_the_query_string_spelling():
    with pytest.raises(InvalidValue) as excinfo:
        FilterBuilder(SCHEMA).add("age", "gte", "old")
    assert excinfo.value.param == "age__gte"


def test_an_empty_builder_is_an_empty_filter():
    assert FilterBuilder(SCHEMA).build() == {}


def test_build_matches_what_parse_produces():
    # The one implementation, reached two ways.
    from_url = parse("age__gte=21&name__contains=ada&tag=red&tag=blue", SCHEMA).filter
    from_builder = (
        FilterBuilder(SCHEMA)
        .add("age", "gte", "21")
        .add("name", "contains", "ada")
        .add("tag", value="red")
        .add("tag", value="blue")
        .build()
    )
    assert from_url == from_builder
