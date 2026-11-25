"""Exception hierarchy. The API maps these to HTTP status codes."""


class RailCoreError(Exception):
    """Base class for all RailCore errors."""

    code = "error"


class ValidationError(RailCoreError):
    code = "validation_error"


class NotFound(RailCoreError):
    code = "not_found"


class BookingClosed(RailCoreError):
    code = "booking_closed"


class NoAvailability(RailCoreError):
    code = "no_availability"


class InvalidState(RailCoreError):
    code = "invalid_state"
