"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_ptr_duplicate_observation as _implementation

sys.modules[__name__] = _implementation
