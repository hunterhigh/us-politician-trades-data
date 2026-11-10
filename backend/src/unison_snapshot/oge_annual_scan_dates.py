"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import oge_annual_scan_dates as _implementation

sys.modules[__name__] = _implementation
