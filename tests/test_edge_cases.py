"""The cases that break hand-rolled filter parsing in production.

Every test here is a bug I have watched happen: a value containing the separator, a value that
looks like a keyword, a list item containing a comma. They are cheap to test and expensive to miss.
"""

import pytest

from qsmongo import Field, InvalidValue, Schema, UnknownField, parse

SCHEMA = Schema(title=Field(str), age=Field(int), tag=Field(str, multi=True))


def test_the_literal_word_and_is_just_a_value():
    assert parse("title=black and white", SCHEMA).filter == {"title": "black and white"}


def test_encoded_ampersand_survives_as_part_of_the_value():
    assert parse("title=Dolce%20%26%20Gabbana", SCHEMA).filter == {"title": "Dolce & Gabbana"}


def test_unencoded_ampersand_splits_the_pair_and_is_reported():
    # There is no way to recover this one: the client must percent-encode. Failing loudly beats
    # silently querying for "Dolce".
    with pytest.raises(UnknownField) as excinfo:
        parse("title=Dolce & Gabbana", SCHEMA)
    assert excinfo.value.param == "Gabbana"


def test_equals_sign_inside_a_value_is_kept():
    assert parse("title=a=b", SCHEMA).filter == {"title": "a=b"}


def test_plus_is_decoded_as_a_space_per_form_encoding():
    assert parse("title=black+white", SCHEMA).filter == {"title": "black white"}


def test_escaped_comma_stays_inside_a_list_item():
    assert parse(r"tag__in=red\,blue,green", SCHEMA).filter == {"tag": {"$in": ["red,blue", "green"]}}


def test_empty_list_items_are_dropped_not_matched_as_empty_strings():
    assert parse("tag__in=red,,green,", SCHEMA).filter == {"tag": {"$in": ["red", "green"]}}


def test_an_entirely_empty_list_is_an_error_rather_than_a_match_nothing_filter():
    with pytest.raises(InvalidValue):
        parse("tag__in=", SCHEMA)


def test_blank_value_on_a_typed_field_is_reported_not_coerced_to_zero():
    with pytest.raises(InvalidValue):
        parse("age=", SCHEMA)


def test_unicode_round_trips():
    assert parse("title=%C5%A1e%C4%87er", SCHEMA).filter == {"title": "šećer"}


def test_empty_query_string_is_a_valid_empty_filter():
    query = parse("", SCHEMA)
    assert query.filter == {}
    assert query.page == 1


def test_leading_question_mark_is_tolerated():
    assert parse("?title=Ada", SCHEMA).filter == {"title": "Ada"}
