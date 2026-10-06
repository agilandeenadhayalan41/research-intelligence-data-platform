"""Validated, explicit environment configuration."""

from research_platform.config.loader import load_config
from research_platform.config.models import PlatformConfig, SampleSelectionConfig

__all__ = ["PlatformConfig", "SampleSelectionConfig", "load_config"]
