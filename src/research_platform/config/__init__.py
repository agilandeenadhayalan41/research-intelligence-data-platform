"""Validated, explicit environment configuration."""

from research_platform.config.loader import load_config
from research_platform.config.models import PlatformConfig

__all__ = ["PlatformConfig", "load_config"]
