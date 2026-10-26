"""Real source graphics-state opacity proofs; no OCR or source parser mocks."""
from collections import Counter
from copy import deepcopy
from io import BytesIO
import json
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from test_native_partition_consumption import normal_pdf
from test_native_bichrome_visibility import inject, source as bichrome_source, TEXT
from unison_snapshot.pdf_native_observer import observe_source_characters
from unison_snapshot import native_opacity_visibility as profile
from unison_snapshot.native_bichrome_visibility import inspect_bichrome_native_page


def opacity_source(kind='fill_zero'):
    """Actual original program, retaining separate gs/font/show/path objects."""
    document=pymupdf.open(stream=normal_pdf('control'),filetype='pdf');page=document[1]
    states={
        'Zero':'<< /Type /ExtGState /ca 0 >>',
        'One':'<< /Type /ExtGState /ca 1 /CA 1 /BM /Normal >>',
        'Stroke':'<< /ca 1 /CA 0 >>',
        'Both':'<< /ca 0 /CA 0 >>',
    }
    bad={'half':'<< /ca .5 >>','blend':'<< /ca 0 /BM /Multiply >>',
         'blend_array':'<< /BM [/Normal] >>','mask':'<< /ca 0 /SMask /None >>',
         'transfer':'<< /ca 0 /TR /Identity >>','transfer2':'<< /TR2 /Default >>',
         'font_state':'<< /Font [/helv 8] >>','shape':'<< /AIS false >>',
         'knockout':'<< /TK true >>','overprint':'<< /OP true >>',
         'bad_alpha_type':'<< /ca true >>','bad_type':'<< /Type (ExtGState) >>',
         'bad_blend_type':'<< /BM (Normal) >>','bad_dictionary':'null'}
    if kind in bad:states['Zero']=bad[kind]
    resources=int(document.xref_get_key(page.xref,'Resources')[1].split()[0]);references={}
    for name,state in states.items():
        ref=document.get_new_xref();document.update_object(ref,state);references[name]=ref
    if kind=='indirect_opaque':
        alpha=document.get_new_xref();document.update_object(alpha,'1')
        blend=document.get_new_xref();document.update_object(blend,'/Normal')
        document.update_object(references['One'],f'<< /ca {alpha} 0 R /BM {blend} 0 R >>')
    document.xref_set_key(resources,'ExtGState','<< '+' '.join(f'/{name} {ref} 0 R' for name,ref in references.items())+' >>')
    needle=TEXT.encode().hex().encode();filer='Filer assets'.encode().hex().encode()
    fill=b'51.25 594 297.5 21 re f'
    for ref in page.get_contents():
        program=document.xref_stream(ref)
        if needle in program:
            if kind in {'opaque','indirect_opaque'}:program=program.replace(b'BT',b'/One gs\nBT',1)
            elif kind=='stroke_zero_text':program=program.replace(b'BT',b'/Stroke gs\nBT',1)
            elif kind=='restore_without_q':program=program.replace(b'BT',b'/Zero gs /One gs\nBT',1)
            elif kind=='inside_text':program=program.replace(b'BT',b'BT /Zero gs',1)
            elif kind=='inside_text_restore':program=program.replace(b'BT',b'BT /Zero gs /One gs',1)
            elif kind=='between_shows':
                show=b'[<'+needle+b'>]TJ'
                program=program.replace(show,b'/Zero gs '+show+b' /One gs 1 0 0 1 52 602 Tm '+show)
            elif kind=='global_restore':program=b'/Zero gs\n'+program
            elif kind=='missing':program=program.replace(b'BT',b'/Missing gs\nBT',1)
            elif kind=='bad_gs_arity':program=program.replace(b'BT',b'/Zero /One gs\nBT',1)
            elif kind not in {'control','unused_state','transparent_fill','opaque_fill','stroke_zero_grid','fill_zero_grid',
                              'invalid_zero_path','zero_path_outside','zero_glyph_outside','empty_shows'}:
                program=program.replace(b'BT',b'/Zero gs\nBT',1)
        if filer in program and kind=='global_restore':program=b'/One gs\n'+program
        document.update_stream(ref,program)
    if kind in {'stroke_zero_grid','fill_zero_grid'}:
        ref=next(ref for ref in page.get_contents() if b' S' in document.xref_stream(ref))
        program=document.xref_stream(ref);name=b'Stroke' if kind=='stroke_zero_grid' else b'Zero'
        document.update_stream(ref,program.replace(b'q',b'q /'+name+b' gs',1))
    if kind in {'transparent_fill','opaque_fill'}:
        name=b'Zero' if kind=='transparent_fill' else b'One'
        inject(document,page,b'q /'+name+b' gs 1 g '+fill+b' Q')
    if kind=='invalid_zero_path':inject(document,page,b'q /Both gs 1 2 m 3 4 l 5 6 l S Q')
    if kind=='zero_path_outside':inject(document,page,b'q /Both gs -10 -10 5 5 re f Q')
    if kind=='zero_glyph_outside':inject(document,page,b'q /Zero gs BT /helv 8 Tf 1 0 0 1 0 0 Tm (j) Tj ET Q')
    if kind=='empty_shows':inject(document,page,b'q /Zero gs BT /helv 8 Tf () Tj [() 0 ()] TJ /One gs () Tj ET Q',before=True)
    raw=document.tobytes();document.close();return raw


class NativeOpacityVisibilityTests(unittest.TestCase):
    def inspect(self,raw,**kwargs):
        with pdfplumber.open(BytesIO(raw)) as document:
            return profile.inspect_opacity_native_page(document.pages[1],**kwargs)

    def field_states(self,result,top=175,bottom=200):
        return Counter(result['glyph_states'][i] for i,char in enumerate(result['chars']) if char['text']!=' ' and
            50<=char['x0'] and char['x1']<=350 and top<=char['top'] and char['bottom']<=bottom)

    def test_zero_alpha_field_keeps_raw_tr_flags_and_independent_visible_row(self):
        result=self.inspect(opacity_source())
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':23})
        self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        self.assertTrue(all(mode==0 for mode in result['render_modes']))
        self.assertTrue(any(result['visibility_flags']))
        self.assertEqual(len(result['chars']),len(result['glyph_states']))
        self.assertEqual(len(result['chars']),len(result['glyph_ink_boxes']))
        self.assertLess(result['budgets']['comparisons_used'],200000)
        self.assertEqual(result['policy_version'],'native-opacity-source-paint/v1')
        self.assertNotIn('eligible',result)
        json.dumps(result,allow_nan=False)

    def test_opaque_stroke_only_and_q_restore_preserve_visible_fields(self):
        for kind in ('opaque','stroke_zero_text','restore_without_q','inside_text_restore','indirect_opaque'):
            with self.subTest(kind=kind):
                result=self.inspect(opacity_source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertEqual(self.field_states(result),{'displayed':23})
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        # Source alpha, rather than the historical sticky flag, proves recovery.
        result=self.inspect(opacity_source('global_restore'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':23})
        self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        independent=[i for i,c in enumerate(result['chars']) if 225<=c['top']<=250]
        self.assertTrue(all(result['visibility_flags'][i] for i in independent))

    def test_gs_inside_bt_and_between_shows_preserves_original_members_and_orders(self):
        result=self.inspect(opacity_source('inside_text'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':23})
        result=self.inspect(opacity_source('between_shows'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':23,'displayed':23})
        self.assertEqual(result['source_program']['gs_events'],result['paint']['graphics_state_events'])
        events=result['source_program']['events'];gs=result['source_program']['gs_events']
        self.assertEqual([x['name'] for x in gs],['Zero','One'])
        self.assertEqual(gs[1]['before_paint_order'],gs[0]['before_paint_order']+1)
        self.assertEqual(events[gs[0]['before_paint_order']-1]['state']['ca'],0)
        self.assertEqual(events[gs[1]['before_paint_order']-1]['state']['ca'],1)
        self.assertEqual(events[gs[1]['before_paint_order']-1]['state']['legacy_visibility_flags'],[profile.LEGACY_GS_FLAG])

    def test_transparent_fill_does_not_cover_text_and_stroke_alpha_selects_only_original_lines(self):
        for kind,state in (('transparent_fill','displayed'),('opaque_fill','occluded')):
            result=self.inspect(opacity_source(kind))
            self.assertEqual(result['status'],'observed',result)
            self.assertEqual(self.field_states(result),{state:23})
            self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        for kind,expected in (('stroke_zero_grid',list(range(1,14))),('fill_zero_grid',list(range(14)))):
            result=self.inspect(opacity_source(kind))
            self.assertEqual(result['status'],'observed',result)
            self.assertEqual(result['displayed_stroke_indices'],expected)
            self.assertEqual(result['source_program']['grid_basis'],'source_bound_opaque_stroke_objects_only')
            self.assertEqual(sum(e['kind']=='stroke' for e in result['source_program']['events']),14)
            self.assertEqual(len(result['paint']['path_events']),14)
            self.assertEqual(self.field_states(result),{'displayed':23})

    def test_exact_actual_render_controls_for_alpha_and_recovery(self):
        baseline=None
        for kind,hidden in (('control',False),('opaque',False),('fill_zero',True),('stroke_zero_text',False),
                            ('restore_without_q',False),('inside_text',True),('inside_text_restore',False),
                            ('between_shows',False),('transparent_fill',False)):
            with pymupdf.open(stream=opacity_source(kind),filetype='pdf') as document:
                page=document[1];box=page.search_for(TEXT)[0]
                pix=page.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=box,colorspace=pymupdf.csGRAY,alpha=False)
                self.assertEqual(min(pix.samples)==255,hidden,kind)
                if kind=='control':baseline=pix.samples
                if not hidden:self.assertEqual(pix.samples,baseline,kind)

    def test_unknown_graphics_states_never_launder_zero_alpha_or_change_old_profile(self):
        for kind in ('half','blend','blend_array','mask','transfer','transfer2','font_state','shape','knockout',
                     'overprint','bad_alpha_type','bad_type','bad_blend_type','bad_dictionary',
                     'missing','bad_gs_arity','invalid_zero_path','zero_path_outside','zero_glyph_outside'):
            with self.subTest(kind=kind):
                result=self.inspect(opacity_source(kind))
                self.assertEqual(result['status'],'unknown',result)
                self.assertNotIn('chars',result)
        # An unreferenced gs dictionary is not an active opacity statement.
        result=self.inspect(opacity_source('unused_state'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(result['paint']['graphics_state_events'],[])
        with pdfplumber.open(BytesIO(opacity_source('opaque'))) as document:
            old=inspect_bichrome_native_page(document.pages[1])
        self.assertEqual(old['status'],'unknown')

    def test_legacy_default_and_path_only_observer_outputs_are_exactly_unchanged(self):
        for kind in ('control','fill_zero','restore_without_q','between_shows','transparent_fill'):
            with pdfplumber.open(BytesIO(opacity_source(kind))) as document:
                page=document.pages[1]
                for paths in (False,True):
                    old=observe_source_characters(page,include_path_events=paths,max_graphics_state_events=-1)
                    new=observe_source_characters(page,include_path_events=paths,include_graphics_state_events=True)
                    reduced=deepcopy(new);reduced[3].pop('graphics_state_events')
                    self.assertEqual(old,reduced)
                    self.assertNotIn('graphics_state_events',old[3])
                if new[3]['graphics_state_events']:
                    with self.assertRaisesRegex(ValueError,'graphics_state_budget_exceeded'):
                        observe_source_characters(page,include_graphics_state_events=True,max_graphics_state_events=0)
                # No new budget is applied to the legacy default call.
                observe_source_characters(page,max_paint_events=-1,max_graphics_state_events=-1)

    def test_empty_shows_keep_paint_order_even_when_zero_alpha(self):
        result=self.inspect(opacity_source('empty_shows'))
        self.assertEqual(result['status'],'observed',result)
        empty=[e for e in result['source_program']['events'] if e['kind']=='text' and e['raw_span'][0]==e['raw_span'][1]]
        self.assertEqual(len(empty),3)
        self.assertEqual([e['state']['ca'] for e in empty],[0,0,1])
        self.assertEqual(result['source_program']['gs_events'][1]['before_paint_order'],3)

    def test_source_gs_and_paint_bindings_reject_tampered_observation(self):
        original=observe_source_characters
        for target in ('gs_name','gs_order','gs_dictionary','path_order','path_flags','character_flag'):
            def tampered(*args,**kwargs):
                result=deepcopy(original(*args,**kwargs))
                if target=='gs_name':result[3]['graphics_state_events'][0]['name']='One'
                elif target=='gs_order':result[3]['graphics_state_events'][0]['before_paint_order']+=1
                elif target=='gs_dictionary':result[3]['graphics_state_events'][0]['state']['ca']=1
                elif target=='path_order':result[3]['path_events'][0]['paint_order']+=1
                elif target=='path_flags':result[3]['path_events'][0]['visibility_flags']=[profile.LEGACY_GS_FLAG]
                else:result[2][0]=[profile.LEGACY_GS_FLAG]
                return result
            with self.subTest(target=target),patch.object(profile,'observe_source_characters',side_effect=tampered):
                result=self.inspect(opacity_source())
                self.assertEqual(result['status'],'unknown',result)

    def test_source_program_budgets_precede_default_character_materialization(self):
        from pdfplumber.page import PDFPageAggregatorWithMarkedContent
        for name,limit in (('MAX_CHARACTERS',10),('MAX_TOKENS',3),('MAX_PAINT_EVENTS',0),
                           ('MAX_PATH_EVENTS',0),('MAX_GRAPHICS_STATE_EVENTS',0),('MAX_GRAPHICS_STATE_RESOURCES',1)):
            with self.subTest(name=name),patch.object(profile,name,limit),patch.object(
                    PDFPageAggregatorWithMarkedContent,'render_char',side_effect=AssertionError('unbounded layout')) as render:
                result=self.inspect(opacity_source())
                self.assertEqual(result['status'],'unknown',result)
                self.assertNotEqual(result['reason'],'native_opacity_inventory_unavailable')
                self.assertEqual(render.call_count,0)
        result=self.inspect(opacity_source(),max_comparisons=0)
        self.assertEqual(result['status'],'unknown',result)
        self.assertNotIn('chars',result)

    def test_graphics_state_reference_resolution_has_explicit_hop_and_cycle_bounds(self):
        from pdfminer.pdftypes import PDFObjRef
        from unison_snapshot.pdf_native_observer import _resolve_graphics_state

        class References:
            def __init__(self):
                self.values={};self.calls=0
            def getobj(self,identifier):
                self.calls+=1
                return self.values[identifier]

        cyclic=References();cyclic.values[1]=PDFObjRef(cyclic,1)
        with self.assertRaisesRegex(ValueError,'reference_budget_exceeded'):
            _resolve_graphics_state(PDFObjRef(cyclic,1))
        self.assertEqual(cyclic.calls,1)
        chained=References()
        chained.values={i:PDFObjRef(chained,i+1) for i in range(1,20)}
        chained.values[20]=1
        with self.assertRaisesRegex(ValueError,'reference_budget_exceeded'):
            _resolve_graphics_state(PDFObjRef(chained,1))
        self.assertEqual(chained.calls,16)
        chained.calls=0
        self.assertEqual(_resolve_graphics_state(PDFObjRef(chained,15)),1)
        self.assertEqual(chained.calls,6)

    def test_existing_bichrome_ink_cases_remain_closed_under_new_profile(self):
        for kind in ('control','white_on_dark','white_on_white','white_then_black','mode3_overlay','partial_fill'):
            with self.subTest(kind=kind):
                raw=bichrome_source(kind);new=self.inspect(raw)
                with pdfplumber.open(BytesIO(raw)) as document:old=inspect_bichrome_native_page(document.pages[1])
                self.assertEqual(new['status'],'observed',new)
                for field in ('chars','render_modes','visibility_flags','glyph_states','glyph_ink_boxes',
                              'displayed_indices','no_effect_indices','occluded_indices','unknown_indices'):
                    self.assertEqual(new[field],old[field],field)


if __name__=='__main__':unittest.main()
