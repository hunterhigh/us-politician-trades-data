"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_table_region as _implementation

sys.modules[__name__] = _implementation
