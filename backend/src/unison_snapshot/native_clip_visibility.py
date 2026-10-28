"""Thin explicit rectangular-clipping mode over the shared native paint proof.

All original glyphs, paths, flags and paint ordinals are conserved. This mode
proves only closed nonzero rectangle requests committed by n; it does not trim
painted paths, infer cells, or grant field qualification.
"""
from .native_source_visibility import _Unsupported
from .native_opacity_visibility import _inspect

POLICY_VERSION = 'native-rectangular-clip-source-paint/v1'
DISPLAY_DOMAIN = 'opaque_white_paper/v1'


def inspect_rectangular_clip_native_page(page, *, max_comparisons=200_000,
                                         display_domain=DISPLAY_DOMAIN):
    try:
        result = _inspect(page, max_comparisons, display_domain, rectangular_clip=True)
        result['policy_version'] = POLICY_VERSION
        return result
    except _Unsupported as error:
        reason = str(error)
    except Exception as error:
        reason = str(error)
        if not reason.startswith(('source_visibility_', 'native_')):
            reason = 'native_rectangular_clip_inventory_unavailable'
    if reason.startswith('native_opacity_'):
        reason = 'native_rectangular_clip_' + reason.removeprefix('native_opacity_')
    return {'status':'unknown', 'reason':reason}
