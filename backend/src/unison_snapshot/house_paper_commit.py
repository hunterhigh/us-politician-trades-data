"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_paper_commit as _implementation

sys.modules[__name__] = _implementation
