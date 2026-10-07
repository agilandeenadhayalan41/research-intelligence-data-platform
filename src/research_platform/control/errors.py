"""Typed errors for pipeline-control contracts (no persistence yet)."""


class ControlError(Exception):
    """Safe base exception for control-plane contract failures."""


class IllegalTransitionError(ControlError, ValueError):
    """A status transition is not allowed by the lifecycle rules."""


class ClaimConflictError(ControlError):
    """A claim token/owner does not match the active claim."""


class StaleClaimError(ControlError):
    """The active claim lease has expired and must be recovered explicitly."""


class ChecksumConflictError(ControlError):
    """Same asset identity registered with a conflicting source checksum."""


class ControlNotFoundError(ControlError):
    """No control row exists for the requested identity."""


class IdempotencyConflictError(ControlError):
    """Registration or completion conflicts with an already-recorded control state."""
