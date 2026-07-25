"""Exceptions raised while parsing a query string.

Every error carries the offending parameter so an API layer can turn it into a useful 400
response instead of a generic "bad request".
"""


class QSMongoError(ValueError):
    """Base class for every parse failure."""

    def __init__(self, message: str, *, param: str | None = None):
        super().__init__(message)
        self.param = param


class UnknownField(QSMongoError):
    """The query string referenced a field that is not declared in the schema."""


class UnsupportedOperator(QSMongoError):
    """The field exists but does not allow this operator."""


class InvalidValue(QSMongoError):
    """A value could not be coerced to the field's declared type, or conflicts with another clause."""


class InvalidPagination(QSMongoError):
    """page / per_page was not a usable positive integer, or two paging modes were mixed."""


class InvalidCursor(QSMongoError):
    """A keyset cursor was malformed, unsigned, forged, or does not match the requested sort."""


class InvalidProjection(QSMongoError):
    """The requested field selection cannot be turned into a MongoDB projection."""
