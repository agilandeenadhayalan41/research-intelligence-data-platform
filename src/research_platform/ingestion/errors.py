"""Safe errors for local Works ingestion."""


class IngestionError(Exception):
    """Base ingestion failure (no credentials or payload dumps)."""


class IngestionConfigError(IngestionError, ValueError):
    """Configuration is not valid for local ingestion."""


class IngestionFormatError(IngestionError, ValueError):
    """Unsupported or malformed source format for ingestion."""


class IngestionDecodeError(IngestionError, ValueError):
    """A source record could not be decoded while streaming."""


class IngestionSkippedError(IngestionError):
    """No eligible file was selected for this run."""


class IngestionRetrievalError(IngestionError):
    """Retrieved source bytes are incomplete or differ from the declared source object.

    Raised before any raw object is landed; a later run re-fetches the asset.
    """


class SourceSizeMismatchError(IngestionRetrievalError):
    """The retrieved byte count differs from the declared size (``RETRIEVAL``)."""


class SourcePayloadIncompleteError(IngestionRetrievalError):
    """A gzip payload ends inside a member (``RETRIEVAL``)."""


class SourceListingMismatchError(IngestionRetrievalError):
    """The source Content-Length differs from the manifest listing (``VALIDATION``)."""


class SourceChecksumMismatchError(IngestionRetrievalError):
    """A single-part source ETag differs from the MD5 of the bytes (``CHECKSUM``)."""


class RawLandingError(IngestionError):
    """The object store failed while landing or verifying raw bytes (``LANDING``).

    The message never carries keys, buckets, projects or provider text; the
    provider exception, when there is one, is chained as ``__cause__``.
    """
