"""Errors raised by ForgeDB's ORM layer."""


class ForgeDBError(Exception):
    """Base class for all ForgeDB errors."""


class SchemaError(ForgeDBError):
    """Raised when a model class definition is invalid."""


class UnknownFieldError(ForgeDBError):
    """Raised when a query references a field the model does not define."""


class UnknownOperatorError(ForgeDBError):
    """Raised when a filter uses an unsupported operator."""


class MissingPrimaryKeyError(ForgeDBError):
    """Raised when an operation requires a primary key the model lacks."""