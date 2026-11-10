"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import oge_278t_source_quad_cells as _implementation

sys.modules[__name__] = _implementation
