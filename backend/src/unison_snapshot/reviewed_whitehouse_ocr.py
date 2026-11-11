"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import reviewed_whitehouse_ocr as _implementation

sys.modules[__name__] = _implementation
