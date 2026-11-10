"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import ruled_cell_blank as _implementation

sys.modules[__name__] = _implementation
