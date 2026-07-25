from datetime import datetime

import pytest

from qsmongo import Field, InvalidValue, Schema, UnknownField, UnsupportedOperator, parse

SCHEMA = Schema(
    name=Field(str),
    email=Field(str, alias="contact.email"),
    age=Field(int),
    score=Field(float),
    active=Field(bool),
    created_at=Field(datetime),
    tag=Field(str, multi=True),
    internal_note=Field(str, sortable=False, ops={"eq"}),
)


def test_equality_is_the_default_operator():
    assert parse("name=Ada", SCHEMA).filter == {"name": "Ada"}


def test_operator_suffixes_become_mongo_operators():
    assert parse("age__gte=21", SCHEMA).filter == {"age": {"$gte": 21}}


def test_operators_on_one_field_merge_into_a_range():
    assert parse("age__gte=21&age__lt=65", SCHEMA).filter == {"age": {"$gte": 21, "$lt": 65}}


def test_values_are_coerced_to_the_declared_type():
    query = parse("age=21&score=1.5&active=yes", SCHEMA)
    assert query.filter == {"age": 21, "score": 1.5, "active": True}
    assert isinstance(query.filter["age"], int)


def test_in_splits_on_commas():
    assert parse("name__in=Ada,Grace", SCHEMA).filter == {"name": {"$in": ["Ada", "Grace"]}}


def test_alias_maps_to_the_document_field():
    assert parse("email=a@b.co", SCHEMA).filter == {"contact.email": "a@b.co"}


def test_exists_takes_a_boolean_regardless_of_field_type():
    assert parse("age__exists=false", SCHEMA).filter == {"age": {"$exists": False}}


def test_unknown_field_is_rejected():
    with pytest.raises(UnknownField) as excinfo:
        parse("password=secret", SCHEMA)
    assert excinfo.value.param == "password"


def test_unknown_field_can_be_ignored_when_asked():
    assert parse("include=profile&name=Ada", SCHEMA, ignore_unknown=True).filter == {"name": "Ada"}


def test_operator_must_be_allowed_for_the_field():
    with pytest.raises(UnsupportedOperator):
        parse("active__gte=true", SCHEMA)


def test_bad_value_names_the_parameter():
    with pytest.raises(InvalidValue) as excinfo:
        parse("age__gte=old", SCHEMA)
    assert excinfo.value.param == "age__gte"


def test_repeated_key_needs_multi():
    assert parse("tag=red&tag=blue", SCHEMA).filter == {"tag": {"$in": ["red", "blue"]}}
    with pytest.raises(InvalidValue):
        parse("name=Ada&name=Grace", SCHEMA)


def test_equality_and_operator_on_the_same_field_conflict():
    with pytest.raises(InvalidValue):
        parse("age=21&age__gte=30", SCHEMA)


def test_dollar_prefixed_keys_cannot_reach_mongo():
    for hostile in ("$where=1", "$ne=1", "age__$gt=1"):
        with pytest.raises(UnknownField):
            parse(hostile, SCHEMA)


def test_values_are_never_interpreted_as_operators():
    # A JSON-looking value stays a string: the parser never evaluates values.
    assert parse('name={"$ne": null}', SCHEMA).filter == {"name": '{"$ne": null}'}
