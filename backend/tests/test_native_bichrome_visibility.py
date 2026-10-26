"""Actual PDF/source programs for the explicit monochrome paint profile."""
from collections import Counter
from copy import deepcopy
from io import BytesIO
import json
import random
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from test_native_partition_consumption import normal_pdf
from unison_snapshot.pdf_native_observer import observe_source_characters
from unison_snapshot import native_bichrome_visibility as profile

TEXT = 'Example Common Stock (EXM)'


def inject(document, page, code, *, before=False):
    reference=document.get_new_xref();document.update_object(reference,'<< >>')
    document.update_stream(reference,code)
    refs=page.get_contents()
    refs=[reference,*refs] if before else [*refs,reference]
    document.xref_set_key(page.xref,'Contents','['+' '.join(f'{ref} 0 R' for ref in refs)+']')


def source(kind='control'):
    document=pymupdf.open(stream=normal_pdf('control'),filetype='pdf');page=document[1]
    for ref in page.get_contents():
        program=document.xref_stream(ref)
        if TEXT.encode().hex().encode() in program.lower() and kind.startswith('white_'):
            document.update_stream(ref,program.replace(b'BT',b'1 g\nBT',1))
    fill=b'51.25 594 297.5 21 re f'
    if kind in {'white_on_dark','white_later_cover','black_on_dark','empty_shows'}:
        inject(document,page,b'q '+fill+b' Q',before=True)
    if kind=='white_re_h_f':
        page.draw_rect(pymupdf.Rect(51.25,177,348.75,198),color=None,fill=(0,0,0),overlay=False)
    if kind=='black_then_white':page.insert_text((52,190),TEXT,fontsize=8,color=(1,1,1))
    if kind=='white_then_black':page.insert_text((52,190),TEXT,fontsize=8,color=(0,0,0))
    if kind=='partial_text':page.insert_text((52,190),'XXXX',fontsize=8,color=(1,1,1))
    if kind=='white_later_cover':inject(document,page,b'q 1 g '+fill+b' Q')
    if kind=='partial_fill':inject(document,page,b'q 1 g 54 605 1 1 re f Q')
    if kind=='empty_shows':inject(document,page,b'q BT /helv 8 Tf () Tj [() 0 ()] TJ ET Q',before=True)
    if kind=='q_restore':inject(document,page,b'q 1 g '+fill+b' Q',before=True)
    if kind=='mode3_overlay':
        previous=set(page.get_contents());page.insert_text((52,190),TEXT,fontsize=8)
        for ref in set(page.get_contents())-previous:
            document.update_stream(ref,document.xref_stream(ref).replace(b'BT',b'BT\n3 Tr',1))
    programs={'negative_width':b'q 348.75 594 -297.5 21 re f Q',
        'negative_height':b'q 51.25 615 297.5 -21 re f Q',
        'multiple_paths':b'q 51.25 594 100 21 re 151.25 594 197.5 21 re f Q',
        'multiple_close':b'q 51.25 594 297.5 21 re h h f Q',
        'f_star':b'q 51.25 594 297.5 21 re f* Q',
        'cm':b'q 1 0 0 1 0 0 cm 51.25 594 297.5 21 re f Q',
        'cross_grid':b'q 50 594 298.75 21 re f Q',
        'nested_BT':b'BT BT ET ET','unmatched_Q':b'Q',
        'bad_arity':b'1 2 3 re f','gray':b'.5 g',
        'CMYK':b'0 0 0 0 k','clipping':b'0 0 100 100 re W n'}
    if kind in programs:inject(document,page,programs[kind],before=True)
    if kind=='opacity':
        ref=document.get_new_xref();document.update_object(ref,'<< /ca 0 /CA 0 >>')
        resources=int(document.xref_get_key(page.xref,'Resources')[1].split()[0])
        document.xref_set_key(resources,'ExtGState',f'<< /S0 {ref} 0 R >>')
        inject(document,page,b'q /S0 gs '+fill+b' Q',before=True)
    if kind=='crop':document.xref_set_key(page.xref,'CropBox','[0 0 100 100]')
    if kind=='group':document.xref_set_key(page.xref,'Group','<< /S /Transparency >>')
    raw=document.tobytes();document.close();return raw


class NativeBichromeVisibilityTests(unittest.TestCase):
    def inspect(self,raw,**kwargs):
        with pdfplumber.open(BytesIO(raw)) as document:
            return profile.inspect_bichrome_native_page(document.pages[1],**kwargs)

    def field_states(self,result,top=175,bottom=200):
        indices=[i for i,char in enumerate(result['chars']) if char['text']!=' ' and
            50<=char['x0'] and char['x1']<=350 and top<=char['top'] and char['bottom']<=bottom]
        return Counter(result['glyph_states'][i] for i in indices)

    def test_positive_contrast_and_no_effect_fields_keep_independent_native_row(self):
        for kind,expected in [('control',{'displayed':23}),('white_on_white',{'no_effect':23}),
            ('white_on_dark',{'displayed':23}),('white_re_h_f',{'displayed':23}),
            ('black_on_dark',{'no_effect':23}),('q_restore',{'displayed':23}),
            ('white_later_cover',{'occluded':23})]:
            with self.subTest(kind=kind):
                result=self.inspect(source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertEqual(self.field_states(result),expected)
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
                self.assertEqual(len(result['chars']),len(result['glyph_states']))
                self.assertEqual(len(result['chars']),len(result['glyph_ink_boxes']))
                self.assertLess(result['budgets']['comparisons_used'],200000)
                self.assertEqual(result['display_domain'],'opaque_white_paper/v1')
                json.dumps(result,allow_nan=False)

    def test_later_text_and_partial_paint_never_grant_a_clean_remaining_word(self):
        for kind in ('black_then_white','partial_text','partial_fill'):
            with self.subTest(kind=kind):
                result=self.inspect(source(kind))
                self.assertEqual(result['status'],'observed',result)
                self.assertTrue(result['unknown_indices'])
                self.assertGreater(self.field_states(result)['unknown'],0)
                self.assertEqual(self.field_states(result,225,250),{'displayed':22})
        result=self.inspect(source('white_then_black'))
        self.assertEqual(self.field_states(result),{'no_effect':23,'displayed':23})
        # A source-cell consumer must require unique raw membership/aliases;
        # this source API deliberately does not call the partial string a fact.
        self.assertNotIn('eligible',result)

    def test_mode3_keeps_original_members_but_never_paints_or_enters_visible_indices(self):
        result=self.inspect(source('mode3_overlay'))
        self.assertEqual(result['status'],'observed',result)
        hidden=[i for i,mode in enumerate(result['render_modes']) if mode==3]
        self.assertEqual(len(hidden),26)
        self.assertTrue(all(result['glyph_states'][i]=='no_effect' for i in hidden))
        self.assertTrue(set(hidden).isdisjoint(result['displayed_indices']))
        self.assertEqual(self.field_states(result),{'displayed':23,'no_effect':23})
        self.assertEqual(result['unknown_indices'],[])

    def test_program_and_observer_orders_include_empty_shows_and_redundant_close(self):
        result=self.inspect(source('empty_shows'))
        self.assertEqual(result['status'],'observed',result)
        events=result['source_program']['events']
        self.assertEqual(sum(e['kind']=='text' and e['raw_span'][0]==e['raw_span'][1] for e in events),2)
        self.assertEqual(len(events),39)
        result=self.inspect(source('white_re_h_f'))
        fills=[p for p in result['paint']['path_events'] if p['fill']]
        self.assertEqual(len(fills),1)
        self.assertEqual([p[0] for p in fills[0]['raw_path']],['m','l','l','l','h','h'])
        self.assertEqual(fills[0]['fill_colorspace'],'DeviceRGB')

    def test_default_observer_outputs_and_old_errors_are_unchanged(self):
        raw=source('white_re_h_f')
        with pdfplumber.open(BytesIO(raw)) as document:
            page=document.pages[1]
            old=observe_source_characters(page)
            explicit=observe_source_characters(page,include_path_events=True)
            self.assertEqual(old[:3],explicit[:3])
            self.assertEqual(old[3],{key:value for key,value in explicit[3].items() if key!='path_events'})
            self.assertNotIn('path_events',old[3])
            self.assertEqual(observe_source_characters(page,max_paint_events=0),old)
            with self.assertRaisesRegex(ValueError,'paint_event_budget_exceeded'):
                observe_source_characters(page,include_path_events=True,max_paint_events=0)
            for arguments,reason in [({'max_characters':1},'character_budget'),({'max_tokens':1},'token_budget')]:
                with self.subTest(arguments=arguments),self.assertRaisesRegex(ValueError,reason):
                    observe_source_characters(page,**arguments)
        from unison_snapshot.native_source_visibility import inspect_standard_visible_page
        with pdfplumber.open(BytesIO(raw)) as document:
            self.assertEqual(inspect_standard_visible_page(document.pages[1])['status'],'unknown')

    def test_unsupported_source_grammar_geometry_and_state_fail_closed(self):
        for kind in ('negative_width','negative_height','multiple_paths','multiple_close','f_star',
                     'cm','cross_grid','nested_BT','unmatched_Q','bad_arity','gray','CMYK',
                     'clipping','opacity','crop','group'):
            with self.subTest(kind=kind):
                result=self.inspect(source(kind))
                self.assertEqual(result['status'],'unknown',result)
                self.assertNotIn('displayed_indices',result)
        for kwargs in ({'display_domain':'unknown'},{'max_comparisons':True},{'max_comparisons':200001}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.inspect(source(),**kwargs)['status'],'unknown')

    def test_observer_snapshot_and_show_binding_cannot_be_signed_by_a_label(self):
        real=observe_source_characters
        def changed(page,**kwargs):
            result=deepcopy(real(page,**kwargs))
            result[3]['path_events'][0]['fill_color']=1
            return result
        with patch.object(profile,'observe_source_characters',side_effect=changed):
            result=self.inspect(source('white_on_dark'))
        self.assertEqual(result['status'],'unknown')
        self.assertIn('path_color_differs',result['reason'])
        def changed_order(page,**kwargs):
            result=deepcopy(real(page,**kwargs));result[3]['character_orders'][0]+=1;return result
        with patch.object(profile,'observe_source_characters',side_effect=changed_order):
            result=self.inspect(source())
        self.assertIn('show_order_differs',result['reason'])

    def test_full_raw_inventory_and_opt_in_paint_exactly_match_source_observation(self):
        raw=source('white_then_black')
        with pdfplumber.open(BytesIO(raw)) as document:
            page=document.pages[1];original=observe_source_characters(page,include_path_events=True)
            result=profile.inspect_bichrome_native_page(page)
            self.assertEqual(tuple(result[key] for key in ('chars','render_modes','visibility_flags','paint')),original)
            self.assertEqual(page.chars,original[0])
            self.assertEqual(profile.inspect_bichrome_native_page(page),result)

    def test_preflight_limits_stop_before_any_glyph_materialization(self):
        from pdfplumber.page import PDFPageAggregatorWithMarkedContent
        original=PDFPageAggregatorWithMarkedContent.render_char;calls=[]
        def counted(device,*args,**kwargs):calls.append(1);return original(device,*args,**kwargs)
        for name,value in [('MAX_CHARACTERS',10),('MAX_TOKENS',10),('MAX_PAINT_EVENTS',1),('MAX_PATH_EVENTS',1),('MAX_FILLS',0)]:
            calls.clear()
            with self.subTest(name=name),patch.object(profile,name,value),patch.object(
                    PDFPageAggregatorWithMarkedContent,'render_char',counted):
                result=self.inspect(source('white_on_dark'))
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(calls,[])
        document=pymupdf.open(stream=source(),filetype='pdf')
        inject(document,document[1],b'BT /helv 8 Tf ('+b'A'*25001+b') Tj ET')
        raw=document.tobytes();document.close();calls.clear()
        with patch.object(PDFPageAggregatorWithMarkedContent,'render_char',counted):
            self.assertEqual(self.inspect(raw)['status'],'unknown')
        self.assertEqual(calls,[])

    def test_actual_dense_native_page_completes_and_budget_failure_has_no_partial_proof(self):
        document=pymupdf.open();document.new_page(width=700,height=792)
        page=document.new_page(width=700,height=792)
        for row in range(50):page.insert_text((30,20+row*14),'A'*75,fontsize=7)
        raw=document.tobytes();document.close()
        result=self.inspect(raw)
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(len(result['displayed_indices']),3750)
        self.assertLess(result['budgets']['comparisons_used'],200000)
        result=self.inspect(raw,max_comparisons=10)
        self.assertEqual(result['status'],'unknown')
        self.assertNotIn('displayed_indices',result)
        self.assertIn('budget_exceeded',result['reason'])

    def test_blank_source_and_active_path_flags_remain_observations(self):
        document=pymupdf.open();document.new_page(width=700,height=792)
        document.new_page(width=700,height=792)
        raw=document.tobytes();document.close()
        result=self.inspect(raw)
        self.assertEqual(result['status'],'observed',result)
        self.assertEqual(result['chars'],[])
        self.assertEqual(result['paint']['path_events'],[])
        # This grants no report/zero-transaction completeness statement.
        self.assertNotIn('coverage_complete',result)
        document=pymupdf.open(stream=source(),filetype='pdf')
        inject(document,document[1],b'q 0 0 10 10 re W n 20 20 5 5 re f Q')
        raw=document.tobytes();document.close()
        with pdfplumber.open(BytesIO(raw)) as pdf:
            observed=observe_source_characters(pdf.pages[1],include_path_events=True)
        self.assertIn('source_visibility_active_clipping_unsupported',
                      observed[3]['path_events'][-1]['visibility_flags'])

    def test_spatial_candidate_index_is_exact_and_huge_domains_stop_before_iteration(self):
        rng=random.Random(7201);boxes=[]
        for _ in range(200):
            x,y=rng.uniform(-50,650),rng.uniform(-50,750)
            boxes.append([x,y,x+rng.uniform(.01,70),y+rng.uniform(.01,70)])
        budget=profile._Budget();index=profile._FootprintIndex(boxes,budget)
        for box in boxes:
            self.assertEqual(set(index.touching(box)),{i for i,other in enumerate(boxes) if profile.overlap(other,box)})
        for boxes,query in [([[-1e100,-1e100,1e100,1e100]],None),
                            ([[1,1,2,2]],[-1e100,-1e100,1e100,1e100]),
                            ([[1,1,10,10]]*20000,[1,1,10,10])]:
            with self.subTest(query=query):
                budget=profile._Budget(10000)
                with self.assertRaisesRegex(ValueError,'budget_exceeded'):
                    index=profile._FootprintIndex(boxes,budget)
                    if query is not None:list(index.touching(query))
                self.assertLessEqual(budget.used,budget.maximum)


if __name__=='__main__':unittest.main()
