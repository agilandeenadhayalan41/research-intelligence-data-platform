"""Errors for OpenAlex canonical modeling."""


class CanonicalModelError(Exception):
    """Base error for canonical OpenAlex modeling failures."""


class IdentifierError(CanonicalModelError, ValueError):
    """An identifier is missing, malformed, or cannot be normalized."""


class MappingError(CanonicalModelError, ValueError):
    """A source record cannot be mapped into the canonical model."""
