"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import oge_annual_scan_cover as _implementation

sys.modules[__name__] = _implementation
