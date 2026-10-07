"""Typed ObjectStore errors for immutable landing adapters."""


class ObjectStoreError(Exception):
    """Safe base exception for object-store failures."""


class InvalidObjectKeyError(ObjectStoreError, ValueError):
    """A storage key is empty, absolute, traversable, or otherwise unsafe."""


class ObjectNotFoundError(ObjectStoreError, FileNotFoundError):
    """No complete committed object exists for the requested key."""


class ObjectConflictError(ObjectStoreError, FileExistsError):
    """An immutable key already holds different content or provenance."""


class ChecksumMismatchError(ObjectStoreError, ValueError):
    """Streamed bytes do not match the caller-supplied provenance checksum."""


class IncompleteObjectError(ObjectStoreError):
    """Committed content and provenance are not both present and readable."""
