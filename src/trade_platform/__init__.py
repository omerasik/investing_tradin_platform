"""Safety-first, paper-only trading intelligence domain package."""

from .config import PlatformConfig
from .risk import RiskEngine

__all__ = ["PlatformConfig", "RiskEngine"]
