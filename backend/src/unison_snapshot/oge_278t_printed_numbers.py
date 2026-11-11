"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import oge_278t_printed_numbers as _implementation

sys.modules[__name__] = _implementation
