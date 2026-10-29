"""Adversarial source PDFs for the closed native administrative-page proof.

These mutate actual content streams/resources and replay saved proofs against
the source. They do not manufacture page-object dictionaries or authorizations.
"""
from copy import deepcopy
from io import BytesIO
import hashlib
from pathlib import Path
import tempfile
import unittest

import pdfplumber
import pymupdf
from PIL import Image

from test_oge_native_cover_roles import native_cover
from test_oge_native_page_roles import native_privacy
from unison_snapshot.oge_278t_page_roles import (
    observe_native_page_role, observe_native_privacy_role, replay_native_page_role)


class NativeCoverProofBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root/'cover.pdf'

    def source(self, **kwargs):
        return native_cover(self.path, **kwargs)

    def rewrite(self, mutate):
        document = pymupdf.open(self.path)
        mutate(document, document[0])
        raw = document.tobytes(); document.close(); self.path.write_bytes(raw)

    def observe(self, context, *, number=1):
        expected = deepcopy(context)
        expected['source_sha256'] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with pdfplumber.open(self.path) as document:
            return observe_native_page_role(document.pages[0], number,
                source_sha256=expected['source_sha256'], cover_identity_context=expected)

    def assert_unknown(self, context):
        proof = self.observe(context)
        self.assertNotEqual(proof['status'], 'non_table', proof)
        self.assertFalse(proof['financial_fact_qualification'])

    def replace_show(self, old, replacement):
        """Replace one real literal/hex show, independent of writer encoding."""
        escaped = old.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)').encode()
        patterns = [(b'('+escaped+b')Tj', 'literal'),
                    (b'('+escaped+b') Tj', 'literal'),
                    (b'[<'+old.encode().hex().encode()+b'>]TJ', 'hex'),
                    (b'<'+old.encode().hex().encode()+b'>Tj', 'hex')]
        def mutate(document, page):
            found = 0
            for xref in page.get_contents():
                raw = document.xref_stream(xref)
                for pattern, encoding in patterns:
                    if pattern in raw:
                        self.assertEqual(raw.count(pattern), 1)
                        raw = raw.replace(pattern, replacement(encoding))
                        document.update_stream(xref, raw)
                        found += 1
                        break
            self.assertEqual(found, 1, 'fixture must expose exactly one full source show for this role')
        self.rewrite(mutate)

    def test_abbreviated_official_name_is_not_a_full_middle_name_assertion(self):
        context = self.source(filer_name='EXAMPLE, ADA ANN')
        context['filer_name'] = 'Example, Ada A'
        original = deepcopy(context)
        proof = self.observe(context)
        self.assertEqual(proof['status'], 'non_table', proof)
        self.assertFalse(proof['financial_fact_qualification'])
        self.assertEqual(context, original)
        self.assertNotIn('person_id', proof)
        self.assertTrue(replay_native_page_role(self.path, proof,
            source_sha256=proof['source_sha256'], page_number=1, cover_identity_context=context))

    def test_same_visible_name_in_two_positioned_runs_is_not_one_source_run(self):
        context = self.source()
        name = context['filer_name']
        split = name.index(',')+2
        with pdfplumber.open(self.path) as document:
            chars = sorted([c for c in document.pages[0].chars if abs(c['top']-159.07)<.001],
                           key=lambda c:c['x0'])
            distance = chars[split]['x0']-chars[0]['x0']
            before = [(c['text'], round(c['x0'], 5), round(c['top'], 5)) for c in chars]
        def replacement(encoding):
            left, right = name[:split].encode(), name[split:].encode()
            if encoding == 'literal':
                return b'('+left+b')Tj\n'+str(distance).encode()+b' 0 Td\n('+right+b')Tj'
            return b'<'+left.hex().encode()+b'>Tj\n'+str(distance).encode()+b' 0 Td\n<'+right.hex().encode()+b'>Tj'
        self.replace_show(name, replacement)
        with pdfplumber.open(self.path) as document:
            after = [(c['text'], round(c['x0'],5), round(c['top'],5)) for c in
                sorted([c for c in document.pages[0].chars if abs(c['top']-159.07)<.001], key=lambda c:c['x0'])]
        self.assertEqual(after, before, 'negative must preserve the visible native glyphs')
        self.assert_unknown(context)

    def test_unaccounted_text_and_white_paint_cannot_hide_inside_static_cover(self):
        for mutation in ['ordinary_text', 'white_text', 'white_box', 'annotation', 'image']:
            with self.subTest(mutation=mutation):
                context = self.source()
                def mutate(document, page):
                    if mutation == 'ordinary_text': page.insert_text((50,510), 'ACME purchase $15,000', fontsize=10)
                    elif mutation == 'white_text': page.insert_text((50,510), 'ACME purchase $15,000', fontsize=10, color=(1,1,1))
                    elif mutation == 'white_box': page.draw_rect(pymupdf.Rect(30,150,815,500), color=None, fill=(1,1,1))
                    elif mutation == 'annotation': page.add_text_annot((50,510), 'ACME purchase $15,000')
                    else:
                        raw = BytesIO(); Image.new('RGB',(10,10),'white').save(raw,format='PNG')
                        page.insert_image(pymupdf.Rect(50,510,60,520),stream=raw.getvalue())
                self.rewrite(mutate)
                self.assert_unknown(context)

    def test_unknown_native_state_operators_are_not_excused_by_matching_glyphs(self):
        for prefix in [b'3 Tr\n', b'0 0 0 0 re W n\n', b'0 Tc\n', b'/K07 gs\n']:
            with self.subTest(prefix=prefix):
                context = self.source()
                def mutate(document, page):
                    if prefix == b'/K07 gs\n':
                        resource = document.xref_get_key(page.xref,'Resources')
                        self.assertEqual(resource[0],'xref')
                        state=document.get_new_xref()
                        document.update_object(state,'<< /Type /ExtGState /ca 0.5 /CA 0.5 >>')
                        document.xref_set_key(int(resource[1].split()[0]),'ExtGState',f'<< /K07 {state} 0 R >>')
                    xref=page.get_contents()[0]
                    document.update_stream(xref,prefix+document.xref_stream(xref))
                self.rewrite(mutate)
                self.assert_unknown(context)

    def test_same_visible_cover_wrapped_in_form_is_outside_native_operator_scope(self):
        context=self.source()
        source=pymupdf.open(self.path)
        document=pymupdf.open(); page=document.new_page(width=842,height=595)
        page.show_pdf_page(page.rect,source,0)
        raw=document.tobytes(); document.close(); source.close(); self.path.write_bytes(raw)
        with pdfplumber.open(self.path) as document:
            self.assertIn('Filer',document.pages[0].extract_text())
        self.assert_unknown(context)

    def test_page_cropbox_cannot_hide_parts_of_an_apparently_complete_native_cover(self):
        context=self.source()
        self.rewrite(lambda document,page:page.set_cropbox(pymupdf.Rect(0,0,842,280)))
        with pdfplumber.open(self.path) as document:
            self.assertNotEqual(tuple(document.pages[0].cropbox),tuple(document.pages[0].bbox))
        self.assert_unknown(context)

    def test_full_ordered_date_role_evidence_replays_from_original_text(self):
        from unison_snapshot.oge_reports import read_278t_cover_dates
        context=self.source(); proof=self.observe(context)
        self.assertEqual(proof['status'],'non_table',proof)
        with pdfplumber.open(self.path) as document:
            text=document.pages[0].extract_text()
        expected=read_278t_cover_dates(text,source_sha256=proof['source_sha256'])
        self.assertEqual(proof['cover_date_evidence'],expected)
        self.assertEqual([item['role'] for item in expected['observations']],
            ['filer_signature','agency_review','other_review','oge_certification','revision_date'])
        altered=deepcopy(proof)
        altered['cover_date_evidence']['observations'][0]['role']='agency_review'
        self.assertFalse(replay_native_page_role(self.path,altered,source_sha256=proof['source_sha256'],
            page_number=1,cover_identity_context=context))

    def test_valid_large_source_has_the_same_observation_and_replay_contract(self):
        from unison_snapshot.oge_reports import MAX_PDF_BYTES
        context=self.source()
        def unpainted_object(document,page):
            xref=document.get_new_xref()
            document.update_object(xref,'<<>>')
            document.update_stream(xref,b'x'*(17*1024*1024),compress=False)
        self.rewrite(unpainted_object)
        self.assertGreater(self.path.stat().st_size,16*1024*1024)
        self.assertLess(self.path.stat().st_size,MAX_PDF_BYTES)
        context['source_sha256']=hashlib.sha256(self.path.read_bytes()).hexdigest()
        proof=self.observe(context)
        self.assertEqual(proof['status'],'non_table',proof)
        self.assertTrue(replay_native_page_role(self.path,proof,
            source_sha256=proof['source_sha256'],page_number=1,cover_identity_context=context))

    def test_unsupported_font_dictionary_does_not_gain_authority_from_same_ascii_text(self):
        for key, value in [('Encoding','/MacRomanEncoding'), ('AuditOnlyMarker','(extra mapping)')]:
            with self.subTest(key=key):
                context = self.source()
                def mutate(document, page):
                    fonts = page.get_fonts(full=True)
                    self.assertTrue(fonts)
                    for font in fonts: document.xref_set_key(font[0],key,value)
                self.rewrite(mutate)
                self.assert_unknown(context)

    def test_extra_date_in_signature_or_revision_band_is_not_deduplicated(self):
        for point in [(600,252),(260,492)]:
            with self.subTest(point=point):
                context = self.source()
                self.rewrite(lambda document,page:page.insert_text(point,'01/02/2026',fontsize=10,fontname='helv'))
                self.assert_unknown(context)

    def test_signature_or_footer_name_cannot_be_rebound_by_self_signed_context(self):
        context = self.source()
        footer = context['filer_name']+' - Page 1'
        def replace(encoding):
            text='OTHER, PERSON - Page 1'
            if encoding == 'literal': return b'('+text.encode()+b')Tj'
            return b'<'+text.encode().hex().encode()+b'>Tj'
        self.replace_show(footer,replace)
        self.assert_unknown(context)
        forged = {**context,'filer_name':'OTHER, PERSON','approved':True,'status':'passed'}
        self.assert_unknown(forged)

    def test_overlong_footer_glyphs_outside_page_never_gain_a_role(self):
        context = self.source(filer_name='W'*60+', '+'W'*60)
        with pdfplumber.open(self.path) as document:
            self.assertTrue(any(c['x0']<0 for c in document.pages[0].chars), 'source must actually overflow left')
        self.assert_unknown(context)

    def test_saved_proof_fields_and_context_never_substitute_for_original_replay(self):
        context=self.source(); proof=self.observe(context)
        self.assertEqual(proof['status'],'non_table',proof)
        for mutate in [lambda p:p.update(rule_version='invented/approved'),
                       lambda p:p.update(source_sha256='0'*64),
                       lambda p:p.update(page_number=2),
                       lambda p:p.update(financial_fact_qualification=True)]:
            altered=deepcopy(proof); mutate(altered)
            self.assertFalse(replay_native_page_role(self.path,altered,
                source_sha256=proof['source_sha256'],page_number=1,cover_identity_context=context))
        for field,value in [('filer_name','OTHER, PERSON'),('agency','Another Office'),
                            ('position_title','Another Role'),('source_sha256','0'*64)]:
            with self.subTest(field=field):
                forged={**context,field:value,'approved':True}
                self.assertFalse(replay_native_page_role(self.path,proof,
                    source_sha256=proof['source_sha256'],page_number=1,cover_identity_context=forged))
        self.path.write_bytes(self.path.read_bytes()+b'\n')
        with self.assertRaises(ValueError):
            replay_native_page_role(self.path,proof,source_sha256=proof['source_sha256'],
                page_number=1,cover_identity_context=context)

    def test_privacy_proof_remains_identical_to_pre_refactor_contract(self):
        native_privacy(self.path)
        sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with pdfplumber.open(self.path) as document:
            proof=observe_native_privacy_role(document.pages[0],1,source_sha256=sha)
        # Frozen before extraction into the shared inventory. These include
        # source event and glyph fingerprints, not only the final status bit.
        self.assertEqual(proof, {'body_character_count':4198,
            'body_layout_sha256':'a945bba71130936fad550b60f29021e516b37228862d0b6f93fe4fcc11c31ce5',
            'content_operator_sha256':'11056daf63aa8e1e1a67d4abfb82e2245068be393c71998074ecc59557452976',
            'financial_fact_qualification':False,
            'footer_layout_sha256':'a6e89e2ff83d8fb4a68843acdb247a0c8b96d2e48278a07904869c087c71747c',
            'footer_source_text_run_count':1,'footer_text':'EXAMPLE, PERSON - Page 1',
            'line_layout_sha256':'cb37e40a51c5c6b6ca797ba914d38ef6fbdfcd21190b8871f0fd1186d0efbb1f',
            'native_character_count':4222,'native_objects_fully_accounted':True,'page_number':1,
            'reason':None,'recognizer_started':False,'role':'fixed_native_privacy_statement_2019',
            'rule_version':'oge-278t-native-privacy-role/v1','source_sha256':sha,'status':'non_table'})

    def test_normal_candidate_quarantines_new_financial_slot_on_source_proved_admin_page(self):
        from test_oge_observed_candidate import original
        from test_oge_content_relations import saved
        from test_oge_candidate import base, catalog, DOCUMENT_ID
        from unison_snapshot.oge_candidate import build_oge_candidate
        native_privacy(self.path)
        self.rewrite(lambda document,page:document.new_page(width=792,height=792))
        sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        old=original(); old['source_sha256']=sha
        old['source_rows'][0]['page_number']=2; old['transactions'][0]['page_number']=2
        before,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
        self.assertEqual(len(before['transactions']),1)
        cells=['2','UNSOURCED ADMIN TRANSACTION','purchase','05/02/2025','No','$1,001 - $15,000']
        observation=saved([cells]); observation['source_sha256']=sha
        observation['pages'][0]['page_number']=1
        candidate,audit=build_oge_candidate(catalog(),[old],before,data_cutoff_at='2025-06-04T00:00:00Z',
            saved_observations_by_document={DOCUMENT_ID:{'observations':[observation],
                'document_page_count':2,'archived_pdf_path':str(self.path)}})
        self.assertEqual(candidate['transactions'],before['transactions'],
            'source-proved administrative page must not originate a new financial fact')
        report=audit['reports'][0]; receipt=report['observation_receipt']
        self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']],[1])
        self.assertFalse(receipt['coverage_complete'])
        self.assertEqual(len(receipt['rows']),1)
        self.assertEqual((receipt['rows'][0]['page_number'],receipt['rows'][0]['slot_key']),(1,'1'))
        self.assertEqual(receipt['rows'][0]['disposition'],'quarantined')
        self.assertEqual(report['quarantined_count'],1)
        self.assertEqual(report['quarantined'][0]['cells'],cells)

    def test_account_heading_on_conflicting_admin_page_cannot_leak_to_later_financial_row(self):
        from test_oge_observed_candidate import original
        from test_oge_content_relations import saved
        from test_oge_candidate import base, catalog, DOCUMENT_ID
        from unison_snapshot.oge_candidate import build_oge_candidate
        for recovered in (False,True):
            with self.subTest(later_independent_heading=recovered):
                native_privacy(self.path)
                def pages(document,page):
                    document.new_page(width=792,height=792); document.new_page(width=792,height=792)
                self.rewrite(pages)
                sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
                old=original(); old['source_sha256']=sha
                old['source_rows'][0]['page_number']=3; old['transactions'][0]['page_number']=3
                before,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
                first=saved([['','Investment account #7','','','','']])
                first['source_sha256']=sha; first['pages'][0]['page_number']=1
                cells=['2','BETA','purchase','05/02/2025','No','$1,001 - $15,000']
                last=saved(([['','Investment account #8','','','','']] if recovered else [])+[cells])
                last['source_sha256']=sha
                observations=[first,last]; original_observations=deepcopy(observations)
                candidate,audit=build_oge_candidate(catalog(),[old],before,data_cutoff_at='2025-06-04T00:00:00Z',
                    saved_observations_by_document={DOCUMENT_ID:{'observations':observations,
                        'document_page_count':3,'archived_pdf_path':str(self.path)}})
                self.assertEqual(observations,original_observations)
                self.assertFalse(any('Investment account #7' in fact['asset_name'] for fact in candidate['transactions']))
                receipt=audit['reports'][0]['observation_receipt']
                self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']],[1])
                self.assertFalse(receipt['coverage_complete'])
                if recovered:
                    beta=[fact for fact in candidate['transactions'] if fact['asset_name'].startswith('BETA')]
                    self.assertEqual([fact['asset_name'] for fact in beta],['BETA — Investment account #8'])
                else:
                    self.assertEqual(candidate['transactions'],before['transactions'])
                    self.assertIn(receipt['rows'][-1]['disposition'],('unresolved','quarantined'))
                    self.assertEqual(audit['reports'][0]['quarantined'][-1]['cells'],cells)

    def test_existing_complete_source_row_fact_survives_admin_role_conflict_verbatim(self):
        from test_oge_observed_candidate import original
        from test_oge_content_relations import saved
        from test_oge_candidate import base, catalog, DOCUMENT_ID
        from unison_snapshot.oge_candidate import build_oge_candidate
        native_privacy(self.path)
        sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        old=original(); old['source_sha256']=sha
        old['transactions'][0]['asset_name']='Example Common Stock See Endnote'
        old['transactions'][0]['cells'][1]='Example Common Stock See Endnote (EXM)'
        old['source_rows'][0]['cells']=deepcopy(old['transactions'][0]['cells'])
        self.assertIn('source_rows',old)
        before,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
        self.assertEqual(len(before['transactions']),1)
        # A real historical classifier matched "note" inside Endnote. Source
        # role conflict is no authority to migrate this prior classification.
        before['transactions'][0]['instrument_type']='Bond'
        for matching in (True,False):
            with self.subTest(saved_slot_matches_original=matching):
                cells=deepcopy(old['transactions'][0]['cells'])
                if not matching:
                    cells[1]='UNRELATED NEW FINANCIAL READING'
                observation=saved([cells]); observation['source_sha256']=sha
                observation['pages'][0]['page_number']=1
                candidate,audit=build_oge_candidate(catalog(),[old],before,data_cutoff_at='2025-06-04T00:00:00Z',
                    saved_observations_by_document={DOCUMENT_ID:{'observations':[observation],
                        'document_page_count':1,'archived_pdf_path':str(self.path)}})
                self.assertEqual(candidate['transactions'],before['transactions'])
                receipt=audit['reports'][0]['observation_receipt']
                self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']],[1])
                if matching:
                    self.assertEqual(receipt['rows'][0]['disposition'],'retained_legacy')
                    self.assertEqual(receipt['rows'][0]['extraction_id'],before['transactions'][0]['id'])
                    self.assertIn('retained_formal_with_source_role_conflict',receipt['rows'][0]['reasons'])
                else:
                    self.assertEqual(receipt['rows'][0]['disposition'],'quarantined')
                    self.assertNotIn('retained_formal_with_source_role_conflict',receipt['rows'][0]['reasons'])
                self.assertFalse(receipt['coverage_complete'])

    def test_continuation_on_source_proved_admin_page_cannot_quarantine_its_financial_parent(self):
        from test_oge_observed_candidate import original
        from test_oge_content_relations import saved
        from test_oge_candidate import base, catalog, DOCUMENT_ID
        from unison_snapshot.oge_candidate import build_oge_candidate
        cases=[('control',True,False),('source_contradicted_continuation',True,True),
               ('genuinely_unresolved_continuation',False,True)]
        for label,administrative,continued in cases:
            with self.subTest(case=label):
                native_privacy(self.path)
                def pages(document,page):
                    if administrative:
                        previous=b'EXAMPLE, PERSON - Page 1'.hex().encode()
                        replacement=b'EXAMPLE, PERSON - Page 3'.hex().encode()
                        matches=0
                        for xref in page.get_contents():
                            raw=document.xref_stream(xref)
                            if previous in raw:
                                document.update_stream(xref,raw.replace(previous,replacement)); matches+=1
                        self.assertEqual(matches,1)
                        document.new_page(width=792,height=792); document.new_page(width=792,height=792)
                        document.select([1,2,0])
                    else:
                        document.delete_page(0)
                        for _ in range(3): document.new_page(width=792,height=792)
                self.rewrite(pages)
                sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
                old=original(); old['source_sha256']=sha
                before,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
                cells=['2','BETA','purchase','05/02/2025','No','$1,001 - $15,000']
                financial=saved([cells]); financial['source_sha256']=sha
                observations=[financial]
                if continued:
                    tail=saved([['','Continued','','','','']]); tail['source_sha256']=sha
                    tail['pages'][0]['page_number']=3; observations.append(tail)
                original_observations=deepcopy(observations)
                candidate,audit=build_oge_candidate(catalog(),[old],before,data_cutoff_at='2025-06-04T00:00:00Z',
                    saved_observations_by_document={DOCUMENT_ID:{'observations':observations,
                        'document_page_count':3,'archived_pdf_path':str(self.path)}})
                self.assertEqual(observations,original_observations)
                receipt=audit['reports'][0]['observation_receipt']
                beta=[fact for fact in candidate['transactions'] if fact['asset_name']=='BETA']
                if administrative:
                    self.assertEqual(len(beta),1,'a source-disproved tail cannot invalidate the preceding disclosure')
                    self.assertEqual(receipt['rows'][0]['disposition'],'materialized')
                    if continued:
                        self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']],[3])
                        self.assertEqual((receipt['rows'][-1]['page_number'],receipt['rows'][-1]['slot_key']),(3,'1'))
                        self.assertEqual(receipt['rows'][-1]['disposition'],'quarantined')
                else:
                    self.assertFalse(beta)
                    self.assertIn('explicit_continuation_relation_unresolved',receipt['rows'][0]['reasons'])
                self.assertFalse(receipt['coverage_complete'])


if __name__=='__main__': unittest.main()
