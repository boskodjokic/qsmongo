"""qsmongo — turn an HTTP query string into a safe MongoDB filter.

from qsmongo import Field, Schema, parse

schema = Schema(name=Field(str), age=Field(int))
query = parse("age__gte=21&sort=-age&page=2", schema)
collection.find(**query.find_kwargs())
"""

from .coerce import coerce
from .errors import InvalidPagination, InvalidValue, QSMongoError, UnknownField, UnsupportedOperator
from .parser import parse
from .query import Query
from .schema import Field, Schema

__version__ = "0.1.0"

__all__ = [
    "Field",
    "InvalidPagination",
    "InvalidValue",
    "QSMongoError",
    "Query",
    "Schema",
    "UnknownField",
    "UnsupportedOperator",
    "coerce",
    "parse",
]
