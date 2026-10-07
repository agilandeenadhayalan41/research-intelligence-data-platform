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
