"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import senate_paper_marks as _implementation

sys.modules[__name__] = _implementation
