"""Original PDF rectangle clipping, paint-state and legacy compatibility contracts."""
from collections import Counter
from copy import deepcopy
from io import BytesIO
import itertools
import json
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from test_native_partition_consumption import normal_pdf
from test_native_bichrome_visibility import source as bichrome_source, inject, TEXT
from test_native_opacity_visibility import opacity_source
from unison_snapshot.pdf_native_observer import observe_source_characters
from unison_snapshot import pdf_native_observer as observer
from unison_snapshot import native_opacity_visibility as shared
from unison_snapshot.native_opacity_visibility import inspect_opacity_native_page
from unison_snapshot.native_clip_visibility import inspect_rectangular_clip_native_page


CLIPS={'inside':b'40 585 300 35 re W n','outside':b'400 585 30 35 re W n',
    'partial':b'80 585 260 35 re W n','outside_page_contains':b'-50 585 400 35 re W n',
    'outside_page_empty':b'800 585 30 35 re W n','outside_page_overlap':b'-50 585 130 35 re W n',
    'empty_intersection':b'400 585 30 35 re W n 40 585 300 35 re W n',
    'nested':b'40 585 300 35 re W n 80 585 260 35 re W n',
    'even_odd':b'40 585 300 35 re W* n','negative':b'340 585 -300 35 re W n',
    'open':b'400 585 30 35 re W','many_requests':b'40 585 300 35 re W W n',
    'bad_arity':b'40 585 300 re W n','no_request':b'40 585 300 35 re n',
    'compound':b'40 585 300 35 re 50 590 20 20 re W n',
    'redundant_h':b'40 585 300 35 re h W n',
    'curve':b'40 585 m 340 585 l 340 620 l 40 620 l h W n',
    'cm':b'1 0 0 1 0 0 cm 40 585 300 35 re W n',
    'commit_fill':b'40 585 300 35 re W f','commit_stroke':b'40 585 300 35 re W S',
    'overflow':b'1'+b'0'*308+b'.0 585 1'+b'0'*308+b'.0 35 re W n'}


def clip_source(kind='inside'):
    base='control'
    if kind in {'white_inside','white_on_dark_inside'}:base='white_on_white' if kind=='white_inside' else 'white_on_dark'
    raw=bichrome_source(base) if base!='control' else normal_pdf('control')
    document=pymupdf.open(stream=raw,filetype='pdf');page=document[1]
    code=CLIPS.get(kind,CLIPS['partial'] if kind=='alpha_zero_partial' else CLIPS['outside']
                   if kind in {'bt_does_not_restore','outside_overlay'} else CLIPS['inside'])
    resources=int(document.xref_get_key(page.xref,'Resources')[1].split()[0])
    if kind in {'alpha_zero_partial','alpha_one_inside','unknown_state'}:
        ref=document.get_new_xref();document.update_object(ref,'<< /ca '+('0' if kind!='alpha_one_inside' else '1')+
            (' /SMask /None' if kind=='unknown_state' else '')+' /CA 1 >>')
        document.xref_set_key(resources,'ExtGState',f'<< /Alpha {ref} 0 R >>')
    for ref in page.get_contents():
        program=document.xref_stream(ref)
        needle=('Investment account #7' if kind=='heading_outside' else 'DESCRIPTION' if kind=='header_outside' else TEXT).encode().hex().encode()
        if needle in program:
            if kind in {'control','path_inside','path_outside','path_partial','fill_partial','empty_shows'}:pass
            elif kind.startswith('text_clip_'):program=program.replace(b'BT',b'BT '+kind[-1:].encode()+b' Tr',1)
            elif kind=='after_show':program=program.replace(b'ET',b'ET '+code,1)
            else:
                if kind in {'heading_outside','header_outside'}:code=CLIPS['outside']
                program=program.replace(b'BT',code+b'\n'+(b'/Alpha gs ' if kind.startswith('alpha_') or kind=='unknown_state' else b'')+b'BT',1)
                if kind=='bt_does_not_restore':program=program.replace(b'ET',b'ET BT /helv 8 Tf 1 0 0 1 52 602 Tm (AFTER ET) Tj ET',1)
            if kind.startswith('miter_'):
                value=kind.removeprefix('miter_')
                if value=='repeat':program=program.replace(b'BT',b'1 M 10 M 100 M BT',1)
                elif value=='inside_bt':program=program.replace(b'BT',b'BT 100 M',1)
                elif value=='without_clip':program=program.replace(CLIPS['inside'],b'10 M')
                elif value in {'1','10','100','0'}:program=program.replace(b'BT',value.encode()+b' M BT',1)
            document.update_stream(ref,program)
    if kind in {'path_inside','path_outside','path_partial'}:
        ref=next(r for r in page.get_contents() if b' S' in document.xref_stream(r));program=document.xref_stream(ref)
        code={'path_inside':b'20 530 20 170 re W n','path_outside':CLIPS['outside'],
              'path_partial':b'29.75 530 20 170 re W n'}[kind]
        document.update_stream(ref,program.replace(b'q',b'q '+code,1))
    if kind=='fill_partial':inject(document,page,b'q '+CLIPS['partial']+b' 0 g 51.25 594 297.5 21 re f Q',before=True)
    if kind=='outside_overlay':page.insert_text((52,190),TEXT,fontsize=8)
    if kind=='miter_multi_stroke':inject(document,page,b'q 10 M 400 400 m 410 400 l 410 410 l S Q')
    if kind=='miter_text_stroke':inject(document,page,b'q BT 10 M /helv 8 Tf 1 Tr 1 0 0 1 52 500 Tm (Outline) Tj ET Q')
    if kind=='empty_shows':inject(document,page,b'q '+CLIPS['outside']+b' BT /helv 8 Tf () Tj [() 0 ()] TJ ET Q',before=True)
    raw=document.tobytes();document.close();return raw


class NativeClipVisibilityTests(unittest.TestCase):
    def inspect(self,raw,**kwargs):
        with pdfplumber.open(BytesIO(raw)) as document:
            return inspect_rectangular_clip_native_page(document.pages[1],**kwargs)

    def field_states(self,result,top=175,bottom=200):
        return Counter(result['glyph_states'][i] for i,c in enumerate(result['chars']) if c['text']!=' ' and
            50<=c['x0'] and c['x1']<=350 and top<=c['top'] and c['bottom']<=bottom)

    def test_whole_ink_inside_outside_and_partial_preserve_original_members_and_other_row(self):
        for kind,expected in (('inside',{'displayed':23}),('outside',{'no_effect':23}),
            ('partial',{'no_effect':6,'unknown':1,'displayed':16}),
            ('nested',{'no_effect':6,'unknown':1,'displayed':16})):
            with self.subTest(kind=kind):
                result=self.inspect(clip_source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertEqual(self.field_states(result),expected)
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
                self.assertEqual(len(result['chars']),229)
                self.assertEqual(result['paint']['paint_event_count'],36)
                self.assertEqual(len(result['paint']['path_events']),14)
                self.assertEqual(len(result['glyph_clip_states']),229)
                self.assertTrue(all(mode==0 for mode in result['render_modes']))
                self.assertTrue(any(result['visibility_flags']))
                self.assertEqual(result['policy_version'],'native-rectangular-clip-source-paint/v1')
                self.assertNotIn('eligible',result)
                json.dumps(result,allow_nan=False)

    def test_domain_external_rectangles_and_empty_intersection_are_positive_evidence(self):
        for kind,expected in (('outside_page_contains',{'displayed':23}),
                              ('outside_page_empty',{'no_effect':23}),
                              ('outside_page_overlap',{'displayed':6,'unknown':1,'no_effect':16}),
                              ('empty_intersection',{'no_effect':23})):
            with self.subTest(kind=kind):
                result=self.inspect(clip_source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertEqual(self.field_states(result),expected)
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
                if kind in {'outside_page_empty','empty_intersection'}:
                    event=next(e for e in result['source_program']['events'] if e['kind']=='text' and
                        e['raw_span'][1]-e['raw_span'][0]==len(TEXT))
                    self.assertIsNone(event['state']['clip_box'])

    def test_q_restores_clip_and_bt_et_does_not_restore_or_paint_after_show(self):
        result=self.inspect(clip_source('bt_does_not_restore'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':30})
        self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        after=self.inspect(clip_source('after_show'))
        self.assertEqual(after['status'],'observed',after)
        self.assertEqual(self.field_states(after),{'displayed':23})
        self.assertEqual(self.field_states(after,225,250),{'displayed':22})
        self.assertEqual(after['paint']['paint_event_count'],36)

    def test_clip_composes_with_existing_bw_alpha_and_unique_visible_overlay(self):
        for kind,expected in (('white_inside',{'no_effect':23}),('white_on_dark_inside',{'displayed':23}),
            ('alpha_zero_partial',{'no_effect':23}),('alpha_one_inside',{'displayed':23}),
            ('outside_overlay',{'no_effect':23,'displayed':23})):
            with self.subTest(kind=kind):
                result=self.inspect(clip_source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertEqual(self.field_states(result),expected)
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        result=self.inspect(clip_source('alpha_zero_partial'))
        target_flags=[set(result['visibility_flags'][i]) for i,c in enumerate(result['chars']) if
            50<=c['x0'] and c['x1']<=350 and 175<=c['top'] and c['bottom']<=200]
        self.assertTrue(all(flags=={shared.LEGACY_GS_FLAG,shared.LEGACY_CLIP_FLAG} for flags in target_flags))

    def test_original_ink_render_controls_for_inside_outside_partial_and_q_restore(self):
        with pymupdf.open(stream=clip_source('control'),filetype='pdf') as d:target=d[1].search_for(TEXT)[0]
        for kind,dark in (('control',3060),('inside',3060),('outside',0),('partial',2265),
                          ('outside_page_contains',3060),('outside_page_empty',0),('outside_page_overlap',795)):
            with self.subTest(kind=kind),pymupdf.open(stream=clip_source(kind),filetype='pdf') as d:
                p=d[1];pix=p.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=target,colorspace=pymupdf.csGRAY,alpha=False)
                self.assertEqual(sum(v<128 for v in pix.samples),dark)
                other=p.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=p.search_for('Other Visible Stock (OTH)')[0],colorspace=pymupdf.csGRAY,alpha=False)
                self.assertEqual(sum(v<128 for v in other.samples),2458)

    def test_clip_requests_and_commits_bind_rawpath_without_incrementing_paint_order(self):
        result=self.inspect(clip_source('inside'))
        self.assertEqual(result['status'],'observed',result)
        events=result['paint']['clipping_events']
        self.assertEqual(result['source_program']['clipping_events'],events)
        self.assertEqual([(x['phase'],x['operator']) for x in events],[('request','W'),('commit','n')])
        self.assertEqual(events[0]['raw_path'],events[1]['raw_path'])
        self.assertEqual(events[0]['before_paint_order'],events[1]['before_paint_order'])
        self.assertEqual(events[0]['raw_path'],[('m',40,585),('l',340,585),('l',340,620),('l',40,620),('h',)])
        self.assertEqual(result['budgets']['clipping_path_point_count'],10)
        empty=self.inspect(clip_source('empty_shows'))
        self.assertEqual(empty['status'],'observed',empty)
        text=[e for e in empty['source_program']['events'] if e['kind']=='text' and e['raw_span'][0]==e['raw_span'][1]]
        self.assertEqual(len(text),2)
        self.assertEqual(empty['paint']['clipping_events'][0]['before_paint_order'],1)
        self.assertEqual(empty['paint']['paint_event_count'],38)

    def test_contributing_paths_must_keep_original_full_footprint_and_line_indices(self):
        result=self.inspect(clip_source('path_inside'))
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(result['displayed_stroke_indices'],list(range(14)))
        for kind in ('path_outside','path_partial','fill_partial'):
            with self.subTest(kind=kind):
                result=self.inspect(clip_source(kind))
                self.assertEqual(result['status'],'unknown',result)
                self.assertIn('contributing_path_clip_unproved',result['reason'])
                self.assertNotIn('displayed_stroke_indices',result)

    def test_unsupported_or_unclosed_clip_cannot_be_laundered_by_deleting_source_path(self):
        for kind in ('even_odd','negative','open','many_requests','bad_arity','no_request','compound',
                     'redundant_h','curve','cm','commit_fill','commit_stroke','overflow','unknown_state',
                     'text_clip_4','text_clip_5','text_clip_6','text_clip_7'):
            with self.subTest(kind=kind):
                result=self.inspect(clip_source(kind))
                self.assertEqual(result['status'],'unknown',result)
                self.assertNotIn('chars',result)
                self.assertNotIn('glyph_states',result)
        for kind,ending in (('commit_fill','f'),('commit_stroke','S')):
            with pdfplumber.open(BytesIO(clip_source(kind))) as d:
                *_,paint=observe_source_characters(d.pages[1],include_clipping_events=True)
            self.assertEqual(paint['clipping_events'][1]['operator'],ending)
            self.assertEqual(paint['clipping_events'][1]['phase'],'commit')

    def test_observer_all_old_switch_combinations_keep_exact_raw_output(self):
        for kind in ('control','inside','outside','partial','alpha_zero_partial','empty_shows'):
            with pdfplumber.open(BytesIO(clip_source(kind))) as d:
                page=d.pages[1]
                for paths,gs in itertools.product((False,True),repeat=2):
                    kw={'include_path_events':paths,'include_graphics_state_events':gs}
                    old=observe_source_characters(page,**kw,max_clipping_events=-1)
                    new=observe_source_characters(page,**kw,include_clipping_events=True)
                    reduced=deepcopy(new);reduced[3].pop('clipping_events')
                    self.assertEqual(old,reduced)
                    self.assertNotIn('clipping_events',old[3])
                if kind!='control':
                    with self.assertRaisesRegex(ValueError,'clipping_event_budget_exceeded'):
                        observe_source_characters(page,include_clipping_events=True,max_clipping_events=0)

    def test_source_proof_rejects_clipping_event_path_phase_commit_and_order_tampering(self):
        actual=observe_source_characters
        for field in ('phase','operator','path','order','missing'):
            def tampered(*args,**kwargs):
                observed=deepcopy(actual(*args,**kwargs));events=observed[3]['clipping_events']
                if field=='phase':events[0]['phase']='commit'
                elif field=='operator':events[1]['operator']='S'
                elif field=='path':events[1]['raw_path'][0]=('m',41,585)
                elif field=='order':events[1]['before_paint_order']+=1
                else:events.pop()
                return observed
            with self.subTest(field=field),patch.object(shared,'observe_source_characters',side_effect=tampered):
                result=self.inspect(clip_source())
                self.assertEqual(result['status'],'unknown',result)
                self.assertIn('clipping_inventory_differs',result['reason'])

    def test_clip_budgets_refuse_before_layout_or_raw_path_copy_and_never_return_half_page(self):
        from pdfplumber.page import PDFPageAggregatorWithMarkedContent
        for constant,value in (('MAX_CLIPPING_EVENTS',1),('MAX_CLIPPING_PATH_POINTS',9),
                               ('MAX_TOKENS',5),('MAX_CHARACTERS',10)):
            with self.subTest(constant=constant),patch.object(shared,constant,value),patch.object(
                    PDFPageAggregatorWithMarkedContent,'render_char',side_effect=AssertionError('unbounded layout')) as render:
                result=self.inspect(clip_source())
                self.assertEqual(result['status'],'unknown',result)
                self.assertNotEqual(result['reason'],'native_rectangular_clip_inventory_unavailable')
                self.assertEqual(render.call_count,0)
        with pdfplumber.open(BytesIO(clip_source())) as d,patch.object(observer,'MAX_CLIPPING_PATH_POINTS',9):
            with self.assertRaisesRegex(ValueError,'clipping_path_budget_exceeded'):
                observe_source_characters(d.pages[1],include_clipping_events=True)
            # The pre-existing path/GS observations have no clipping-copy quota.
            observe_source_characters(d.pages[1],include_path_events=True,include_graphics_state_events=True)
        for maximum in (0,10,100):
            result=self.inspect(clip_source(),max_comparisons=maximum)
            self.assertEqual(result['status'],'unknown',result)
            self.assertNotIn('chars',result)

    def test_miter_is_semantically_inert_only_for_the_new_closed_no_join_paint_scope(self):
        renders=[]
        for kind in ('miter_1','miter_10','miter_100','miter_repeat','miter_inside_bt'):
            raw=clip_source(kind)
            result=self.inspect(raw)
            self.assertEqual(result['status'],'observed',result)
            self.assertEqual(self.field_states(result),{'displayed':23})
            self.assertEqual(self.field_states(result,225,250),{'displayed':22})
            target=next(e for e in result['source_program']['events'] if e['kind']=='text' and
                e['raw_span'][1]-e['raw_span'][0]==len(TEXT))
            self.assertEqual(target['state']['miter_limit'],1 if kind=='miter_1' else
                             10 if kind=='miter_10' else 100)
            with pymupdf.open(stream=raw,filetype='pdf') as d:
                renders.append(d[1].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).samples)
        self.assertTrue(all(pixels==renders[0] for pixels in renders))
        for kind in ('miter_0','miter_multi_stroke','miter_text_stroke'):
            result=self.inspect(clip_source(kind))
            self.assertEqual(result['status'],'unknown',result)
        with pdfplumber.open(BytesIO(clip_source('miter_without_clip'))) as d:
            old=inspect_opacity_native_page(d.pages[1])
        self.assertEqual(old,{'status':'unknown','reason':'native_opacity_unsupported_op:M'})

    def test_unsupported_curve_observation_counts_all_control_points_before_copying(self):
        document=pymupdf.open(stream=normal_pdf('control'),filetype='pdf')
        inject(document,document[1],b'q 1 1 m 2 3 4 5 6 7 c W n Q',before=True)
        raw=document.tobytes();document.close()
        with pdfplumber.open(BytesIO(raw)) as d:
            observed=observe_source_characters(d.pages[1],include_clipping_events=True)
        event=observed[3]['clipping_events'][0]
        self.assertEqual(len(event['raw_path']),2)
        self.assertEqual(observer._clipping_path_weight(event['raw_path']),4)
        # Two raw components are four control points. The second copy must
        # exceed a six-point quota although merely counting components would pass.
        import copy
        with pdfplumber.open(BytesIO(raw)) as d,patch.object(observer,'MAX_CLIPPING_PATH_POINTS',6),patch(
                'copy.deepcopy',wraps=copy.deepcopy) as copying:
            with self.assertRaisesRegex(ValueError,'clipping_path_budget_exceeded'):
                observe_source_characters(d.pages[1],include_clipping_events=True)
            copied_paths=[call.args[0] for call in copying.call_args_list if call.args and
                isinstance(call.args[0],list) and call.args[0] and call.args[0][0]==('m',1,1)]
            self.assertEqual(len(copied_paths),1)
        result=self.inspect(raw)
        self.assertEqual(result['status'],'unknown',result)

    def test_clip_numeric_domain_matches_real_rendering_without_a_magnitude_threshold(self):
        def rectangle_source(code):
            document=pymupdf.open(stream=normal_pdf('control'),filetype='pdf');page=document[1]
            for ref in page.get_contents():
                program=document.xref_stream(ref)
                if TEXT.encode().hex().encode() in program:
                    document.update_stream(ref,program.replace(b'BT',code+b' re W n BT',1))
            raw=document.tobytes();document.close();return raw

        # All double operands/edges are finite, but the renderer's numeric
        # representation overflows. It visibly leaves some target ink; this
        # cannot be signed as a mathematically empty, nonpainting clip.
        raw=rectangle_source(b'1'+b'0'*40+b'.0 585 100 35')
        with pymupdf.open(stream=normal_pdf('control'),filetype='pdf') as document:
            target=document[1].search_for(TEXT)[0]
        with pymupdf.open(stream=raw,filetype='pdf') as document:
            pixels=document[1].get_pixmap(matrix=pymupdf.Matrix(4,4),clip=target,
                colorspace=pymupdf.csGRAY,alpha=False).samples
        self.assertGreater(sum(v<128 for v in pixels),0)
        self.assertLess(sum(v<128 for v in pixels),3060)
        result=self.inspect(raw)
        self.assertEqual(result['status'],'unknown',result)
        self.assertIn('clipping_numeric_domain_unsupported',result['reason'])
        # This is a precision domain, not an arbitrary coordinate ceiling.
        large=2**100
        exact=rectangle_source(f'{large} 585 {large} 35'.encode())
        result=self.inspect(exact)
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(self.field_states(result),{'no_effect':23})
        self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        with pymupdf.open(stream=exact,filetype='pdf') as document:
            pixels=document[1].get_pixmap(matrix=pymupdf.Matrix(4,4),clip=target,
                colorspace=pymupdf.csGRAY,alpha=False).samples
        self.assertEqual(min(pixels),255)
        for code in (b'40.1 585 299.9 35',b'10000000000 585 100 35'):
            result=self.inspect(rectangle_source(code))
            self.assertEqual(result['status'],'unknown',result)
            self.assertIn('clipping_numeric_domain_unsupported',result['reason'])
        dyadic=self.inspect(rectangle_source(b'40.125 585 299.875 35'))
        self.assertEqual(dyadic['status'],'observed',dyadic)
        self.assertEqual(self.field_states(dyadic),{'displayed':23})
        self.assertEqual(dyadic['source_program']['clipping_numeric_domain'],
                         'float32_exact_source_rectangle/v1')

    def test_old_opacity_profile_remains_exact_and_has_no_new_clip_fields(self):
        for kind in ('control','fill_zero','stroke_zero_text','restore_without_q','between_shows','transparent_fill'):
            raw=opacity_source(kind)
            with pdfplumber.open(BytesIO(raw)) as d:
                old=inspect_opacity_native_page(d.pages[1]);new=inspect_rectangular_clip_native_page(d.pages[1])
            self.assertEqual(old['status'],'observed',old)
            self.assertEqual(new['status'],'observed',new)
            self.assertNotIn('glyph_clip_states',old)
            self.assertNotIn('clipping_events',old['paint'])
            self.assertNotIn('clipping_events',old['source_program'])
            self.assertTrue(all('clip_box' not in e['state'] for e in old['source_program']['events']))
            for field in ('chars','render_modes','visibility_flags','glyph_states','glyph_ink_boxes',
                          'displayed_indices','no_effect_indices','unknown_indices','displayed_stroke_indices'):
                self.assertEqual(old[field],new[field],field)
        with pdfplumber.open(BytesIO(clip_source('inside'))) as d:
            old=inspect_opacity_native_page(d.pages[1])
        self.assertEqual(old,{'status':'unknown','reason':'native_opacity_unsupported_op:W'})


if __name__=='__main__':unittest.main()
