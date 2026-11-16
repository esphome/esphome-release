class EsphomeReleaseError(Exception):
    """Base error for EsphomeRelease errors."""


class ConfigNotFoundError(EsphomeReleaseError):
    """Raised when config.json is missing from the current directory."""
