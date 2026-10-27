"""Generated note/target controls; no actual filing payload is embedded."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

import pdfplumber
import pymupdf

from test_oge_summary_region import summary_pdf, _text
from unison_snapshot.oge_278t_endnote_region import (
    observe_native_endnote_region, replay_native_endnote_region,
)


def endnote_pdf(path, *, page_number=1, targets=('41','42','43','48'),
                note_numbers=('41','42','43','48'), part='Transactions',
                mutation=None, footer_name='EXAMPLE, ADA', upper_program=None,
                add_competing_page=False):
    """One source page with public Summary, synthetic targets and raw notes.

    Pass page_number=2 before inserting this page after an independently
    generated cover. Recompute the complete source SHA after any insertion.
    upper_program may replace only the synthetic financial area. It is not
    used to supply a precomputed reference or authorization receipt.
    """
    edges = [36, 65, 360, 430, 500, 610, 806]
    program = []
    if upper_program is None:
        summary_control = tuple(targets) == ('1',)
        ys = [76, 106, 136] if summary_control else [36, 66, *[91+25*i for i in range(len(targets))]]
        program += ['q 2 J 0 G 1 w']
        for top in ys:
            for left, right in zip(edges, edges[1:]):
                program.append(f'{left} {595-top} m {right} {595-top} l S')
        program += ['Q']
        for text, x in zip(('#','DESCRIPTION','TYPE','DATE','NOTIFICATION','AMOUNT'), edges):
            program.append(_text(text, x+2, 500 if summary_control else 550))
        for index, number in enumerate(targets):
            for text, x in zip((number, 'Example Company', 'Purchase', '06/01/2026',
                                'No', '$1,001 - $15,000'), edges):
                program.append(_text(text, x+2, 470 if summary_control else 518-25*index))
    else:
        program.append(upper_program)
    program.append(_text('Endnotes', 36, 373, 14))
    for text, x in (('PART',41), ('#',137.25), ('ENDNOTE',329.75)):
        program.append(_text(text, x, 351))
    note_edges = [36,132.25,324.75,806]
    program += ['q 2 J 0 G 1 w']
    for top in [254+30*i for i in range(len(note_numbers)+1)]:
        for left, right in zip(note_edges, note_edges[1:]):
            program.append(f'{left} {595-top} m {right} {595-top} l S')
    program += ['Q']
    for index, number in enumerate(note_numbers):
        y = 328-30*index
        for text, x in ((part,41), (number,137.25),
                        (f'Synthetic note for source row {number}.',329.75)):
            program.append(_text(text, x, y, 12 if mutation=='local_note_12' and x==329.75 else 10))
        program.append(_text('Its financial effect remains unknown.',329.75,y-11))
    if mutation == 'extra_text': program.append(_text('X',810,328))
    elif mutation == 'crossing_cell': program.append(_text('XX',320,328))
    elif mutation == 'competing_paint': program.append('q 2 J 1 w 40 330 m 800 320 l S Q')
    elif mutation == 'gap_continuation': program.append(_text('CONTINUED',36,210))
    elif mutation == 'missing_header': program = [s.replace('(PART)Tj','(SECTION)Tj') for s in program]
    elif mutation == 'wrong_cap': program = [s.replace('2 J','1 J') for s in program]
    data = summary_pdf(path,page_number=page_number,footer_name=footer_name,
                       upper_program='\n'.join(program),
                       mutation='white_cover' if mutation=='white_cover' else None)
    if add_competing_page:
        document=pymupdf.open(path);page=document.new_page(width=842,height=595)
        page.insert_font(fontname='helv');xref=document.get_new_xref();document.update_object(xref,'<<>>')
        document.update_stream(xref,_text('Unexplained source material',36,500).encode('ascii'));page.set_contents(xref)
        raw=document.tobytes();document.close();Path(path).write_bytes(raw)
        data.update(raw=raw,source_sha256=hashlib.sha256(raw).hexdigest())
    data.update(note_numbers=list(note_numbers),target_numbers=list(targets),
                upper_row_band_pdf_points=[106,136] if tuple(targets)==('1',) else [66,91])
    return data


def number_cell_upper_program(kind):
    """An actual cell, with continuous multi-run digits or competing ink."""
    edges=[36,65,360,430,500,610,806]
    program=['q 2 J 0 G 1 w']
    for y in (519,489,459):
        for left,right in zip(edges,edges[1:]):
            program.append(f'{left} {y} m {right} {y} l S')
    program.append('Q')
    for label,x in zip(('#','DESCRIPTION','TYPE','DATE','NOTIFICATION','AMOUNT'),edges):
        program.append(_text(label,x+2,500))
    if kind in ('split','gap'):
        advance='5.56' if kind=='split' else '9'
        program.append(f'BT /helv 10 Tf 38 470 Td (4)Tj {advance} 0 Td (1)Tj ET')
    else:
        program.append(_text('4',62 if kind=='crossing' else 38,470))
    for text,x in zip(('Example Company','Purchase','06/01/2026','No','$1,001 - $15,000'),edges[1:]):
        program.append(_text(text,x+2,470))
    if kind=='drawn':program.append('q 1 w 46 470 m 46 478 l S Q')
    return '\n'.join(program)


class NativeEndnoteRegionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'source.pdf'

    def observe(self,data,number=1,page_index=0,**kwargs):
        with pdfplumber.open(self.path) as pdf:
            return observe_native_endnote_region(pdf.pages[page_index],number,
                source_sha256=data['source_sha256'],**kwargs)

    def test_complete_dynamic_notes_bind_only_same_source_physical_locators(self):
        data=endnote_pdf(self.path);before=self.path.read_bytes();proof=self.observe(data)
        self.assertEqual(proof['status'],'observed',proof)
        self.assertEqual(proof['role'],'native_referential_endnotes')
        self.assertEqual(proof['page_coverage'],'partial')
        self.assertEqual(proof['financial_effect'],'unknown')
        self.assertFalse(proof['financial_fact_qualification'])
        self.assertEqual(proof['column_edges_pdf_points'],[36,132.25,324.75,806])
        self.assertEqual([note['printed_number']for note in proof['notes']],data['note_numbers'])
        for note in proof['notes']:
            self.assertEqual(len(note['raw_text_lines']),2)
            self.assertIn('\n',note['raw_text'])
            self.assertEqual(note['reference']['status'],'source_bound')
            target=note['reference']['target']
            self.assertEqual(target['source_sha256'],data['source_sha256'])
            self.assertEqual(target['printed_number'],note['printed_number'])
            self.assertEqual(target['page_number'],1)
            self.assertNotIn('extraction_id',target)
            self.assertTrue(all(cell['source_run_refs']for cell in note['cells']))
        self.assertTrue(replay_native_endnote_region(self.path,proof,
            source_sha256=data['source_sha256'],page_number=1))
        self.assertEqual(self.path.read_bytes(),before)

    def test_missing_duplicate_or_unknown_scope_preserves_raw_notes_without_bound_reference(self):
        for options,reason in (({'targets':('42','43','48')},'target_number_missing'),
                ({'targets':('41','41','43','48')},'target_number_not_unique'),
                ({'add_competing_page':True},'document_reference_scope_unknown'),
                ({'part':'Unclassified'},'unsupported_reference_part'),
                ({'note_numbers':('41/42',)},'unsupported_reference_number')):
            with self.subTest(options=options):
                data=endnote_pdf(self.path,**options);proof=self.observe(data)
                self.assertEqual(proof['status'],'observed',proof)
                note=proof['notes'][0]
                self.assertEqual(note['reference']['status'],'unknown')
                self.assertEqual(note['reference']['reason'],reason)
                self.assertTrue(note['raw_text_lines'])
                self.assertFalse(proof['financial_fact_qualification'])
                self.assertEqual(note['raw_number'],options.get('note_numbers',('41',))[0])

    def test_unexplained_crossing_or_opaque_region_members_reject_the_role(self):
        for mutation in ('extra_text','crossing_cell','competing_paint',
                         'gap_continuation','missing_header','white_cover','wrong_cap'):
            with self.subTest(mutation=mutation):
                data=endnote_pdf(self.path,mutation=mutation);proof=self.observe(data)
                self.assertEqual(proof['status'],'unknown',proof)
                self.assertNotIn('notes',proof)

    def test_replay_binds_source_members_targets_and_original_context_reading_mode(self):
        data=endnote_pdf(self.path);proof=self.observe(data)
        for mutate in ('target','text','members','context'):
            bad=deepcopy(proof)
            if mutate=='target':bad['notes'][0]['reference']['target']['band_pdf_points']=[0,595]
            elif mutate=='text':bad['notes'][0]['raw_text']='FORGED NOTE'
            elif mutate=='members':bad['notes'][0]['cells'][0]['character_indices']=[]
            else:bad['reference_cover_context_used']=True;bad['reference_cover_context_sha256']='0'*64
            with self.subTest(mutate=mutate):
                self.assertFalse(replay_native_endnote_region(self.path,bad,
                    source_sha256=data['source_sha256'],page_number=1))
        # A proof initially read without identity context cannot silently
        # upgrade its reference scope when a later caller supplies context.
        self.assertTrue(replay_native_endnote_region(self.path,proof,
            source_sha256=data['source_sha256'],page_number=1,
            cover_identity_context={'untrusted_later_context':True}))
        context={'schema_version':'oge-278t-cover-identity-context/v1',
            'source_sha256':data['source_sha256'],'filer_name':'EXAMPLE, ADA',
            'agency':'Example Agency','position_title':'Analyst'}
        bound=self.observe(data,cover_identity_context=context)
        self.assertTrue(bound['reference_cover_context_used'])
        self.assertFalse(replay_native_endnote_region(self.path,bound,
            source_sha256=data['source_sha256'],page_number=1))
        self.assertTrue(replay_native_endnote_region(self.path,bound,
            source_sha256=data['source_sha256'],page_number=1,cover_identity_context=context))
        self.assertFalse(replay_native_endnote_region(self.path,bound,
            source_sha256=data['source_sha256'],page_number=1,
            cover_identity_context={**context,'position_title':'Different'}))
        with self.assertRaises(ValueError):
            replay_native_endnote_region(self.path,proof,source_sha256='0'*64,page_number=1)
        # Mature administrative fonts are handled by their existing complete
        # page profile before the narrower transaction font lane is applied.
        from test_oge_native_cover_roles import native_cover
        cover=Path(self.temporary.name)/'cover.pdf';context=native_cover(cover)
        data=endnote_pdf(self.path,page_number=2)
        document=pymupdf.open(cover)
        with pymupdf.open(stream=data['raw'],filetype='pdf') as notes:document.insert_pdf(notes)
        combined=document.tobytes();document.close();self.path.write_bytes(combined)
        data.update(raw=combined,source_sha256=hashlib.sha256(combined).hexdigest())
        context={**context,'source_sha256':data['source_sha256']}
        unbound=self.observe(data,2,page_index=1)
        self.assertEqual(unbound['status'],'observed',unbound)
        self.assertTrue(all(n['reference']['status']=='unknown'for n in unbound['notes']))
        bound=self.observe(data,2,page_index=1,cover_identity_context=context)
        self.assertEqual(bound['status'],'observed',bound)
        self.assertTrue(all(n['reference']['status']=='source_bound'for n in bound['notes']))
        self.assertTrue(replay_native_endnote_region(self.path,bound,
            source_sha256=data['source_sha256'],page_number=2,cover_identity_context=context))

    def test_complete_number_cell_members_combine_runs_but_never_ignore_other_ink(self):
        for kind,number,expected in (('split','41','source_bound'),('split','4','unknown'),
                                     ('drawn','4','unknown'),('gap','41','unknown'),
                                     ('crossing','4','unknown')):
            with self.subTest(kind=kind,number=number):
                data=endnote_pdf(self.path,note_numbers=(number,),
                                  upper_program=number_cell_upper_program(kind))
                proof=self.observe(data)
                self.assertEqual(proof['status'],'observed',proof)
                reference=proof['notes'][0]['reference']
                self.assertEqual(reference['status'],expected,reference)
                self.assertEqual(proof['notes'][0]['raw_number'],number)
                if expected=='source_bound':
                    target=reference['target']
                    self.assertEqual(target['printed_number'],'41')
                    self.assertEqual(target['number_cell_bbox_pdf_points'],[36,106,65,136])
                    self.assertEqual(len(target['number_character_indices']),2)
                    self.assertEqual(len(target['number_source_run_refs']),2)
        # Parent-only space sizing is not an extension of the local note
        # glyph lane. Both the space and its actual font/bbox remain in the
        # original source inventory and raw ASCII binding.
        for text,expected in ((' ','source_bound'),('7','unknown')):
            data=endnote_pdf(self.path,note_numbers=('41',),upper_program='')
            parent=Path(self.temporary.name)/'parent.pdf'
            parent_document=pymupdf.open();parent_page=parent_document.new_page(width=842,height=595)
            parent_page.insert_font(fontname='helv')
            xref=parent_document.get_new_xref();parent_document.update_object(xref,'<<>>')
            program=number_cell_upper_program('split')+'\n'+_text(text,802.668,469,12)
            parent_document.update_stream(xref,program.encode('ascii'));parent_page.set_contents(xref)
            parent_document.save(parent);parent_document.close()
            document=pymupdf.open(self.path)
            with pymupdf.open(parent) as other:document.insert_pdf(other)
            combined=document.tobytes();document.close();self.path.write_bytes(combined)
            data.update(raw=combined,source_sha256=hashlib.sha256(combined).hexdigest())
            proof=self.observe(data)
            self.assertEqual(proof['status'],'observed',proof)
            self.assertEqual(proof['notes'][0]['reference']['status'],expected,proof)
        data=endnote_pdf(self.path,mutation='local_note_12')
        self.assertEqual(self.observe(data)['status'],'unknown')


if __name__=='__main__':
    unittest.main()
