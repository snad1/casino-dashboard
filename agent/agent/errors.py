"""Failures a command handler reports with a generic code (see protocol.ErrorCode)."""


class Busy(Exception):
    """Another mutating job runs for this service; nothing was started."""


class NotFound(Exception):
    """No job with that id (it never existed or was pruned)."""
