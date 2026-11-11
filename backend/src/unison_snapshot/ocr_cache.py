"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import ocr_cache as _implementation

sys.modules[__name__] = _implementation
