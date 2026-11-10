"""Compatibility import for the frozen paper implementation."""
import sys
from .frozen_paper import house_image_body_routing as _implementation

sys.modules[__name__] = _implementation
