"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_paper_temporal as _implementation

sys.modules[__name__] = _implementation
