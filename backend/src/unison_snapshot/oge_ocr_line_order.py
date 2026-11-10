"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import oge_ocr_line_order as _implementation

sys.modules[__name__] = _implementation
