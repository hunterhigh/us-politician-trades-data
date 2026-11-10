"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_amount_header as _implementation

sys.modules[__name__] = _implementation
