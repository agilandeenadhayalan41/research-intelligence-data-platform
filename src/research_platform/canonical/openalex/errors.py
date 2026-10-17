"""Errors for OpenAlex canonical modeling."""


class CanonicalModelError(Exception):
    """Base error for canonical OpenAlex modeling failures."""


class IdentifierError(CanonicalModelError, ValueError):
    """An identifier is missing, malformed, or cannot be normalized."""


class MappingError(CanonicalModelError, ValueError):
    """A source record cannot be mapped into the canonical model."""


class CanonicalExportError(CanonicalModelError):
    """Canonical rows cannot be exported to the Arrow/Parquet interchange contract."""


class CanonicalReleaseError(CanonicalModelError):
    """Base error for Silver canonical releases (DE-07). Messages carry no keys or values."""


class ReleaseConfigError(CanonicalReleaseError, ValueError):
    """Release build arguments are invalid (writer settings, ids, input list or order)."""


class ReleaseInputError(CanonicalReleaseError):
    """A Bronze input is missing, or differs from its landing or release manifest."""


class ReleaseRebuildError(CanonicalReleaseError):
    """Replaying or rendering the listed Bronze inputs failed (mapping, counts, barriers)."""


class ReleaseParentError(CanonicalReleaseError):
    """The parent release is missing, not older, or its inputs are not a prefix."""


class ReleaseConflictError(CanonicalReleaseError):
    """The release id or record key already holds different content or intent; it is kept."""


class ReleaseNotFoundError(CanonicalReleaseError):
    """No manifest exists for the release id: the release is not visible."""


class ReleaseManifestError(CanonicalReleaseError):
    """The release manifest is incomplete, unreadable, non-canonical or invalid."""


class ReleaseSchemaMismatchError(CanonicalReleaseError):
    """``schema_major`` or table fingerprints differ from this code where bytes must match."""


class ReleasePartError(CanonicalReleaseError):
    """A listed part is missing, corrupt, or differs from the manifest."""


class ReleaseEnvironmentMismatchError(CanonicalReleaseError):
    """Byte reproduction needs the PyArrow version and Parquet options recorded in the manifest."""


class ReconciliationMismatchError(CanonicalReleaseError):
    """A rebuild from Bronze did not reproduce the release."""


class PublicationRecordError(CanonicalReleaseError):
    """A publication record is invalid or names a manifest whose SHA-256 differs."""
