"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_fixed_source_core as _implementation

sys.modules[__name__] = _implementation
