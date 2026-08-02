class CoreError(Exception):
    """Base error with an HTTP-friendly status code."""

    status_code = 400


class ValidationError(CoreError):
    status_code = 422


class AuthenticationError(CoreError):
    status_code = 401


class NotFoundError(CoreError):
    status_code = 404


class ConflictError(CoreError):
    status_code = 409
