"""Fixed government cover layout and typed source fields, never a report list."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pdfplumber
import pymupdf


TEMPLATE = Path(__file__).parent / 'fixtures/oge_cover_2019_template.json'


def native_cover(path, *, filer_name='EXAMPLE, ADA', position_title='Senior Analyst',
                 agency='Economic Office', reviewers=None, mutation=None):
    """Write a real native PDF; every variable has an explicit source role.

    One content stream uses literal ASCII Tj strings, one BT/ET block per line.
    Independent tests may edit the real stream or append unsupported objects.
    """
    template = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    reviewers = reviewers or ['REVIEWER, ALICE', 'REVIEWER, BOB', 'REVIEWER, CAROL']
    names = [filer_name, *reviewers]
    dates = ['06/13/2026', '06/14/2026', '06/15/2026', '06/16/2026']
    roles = ['filer_signature', 'agency_review', 'other_review', 'oge_certification']
    values = {'filer_name': filer_name, 'position_agency': position_title + ', ' + agency,
              'revision_date': 'Data Revised 06/17/2026', 'footer': filer_name + ' - Page 1'}
    for i, role in enumerate(roles):
        office = '' if i == 0 else ', Ethics Official' if i == 2 else ', Certifying Official'
        values[role] = f'/s/ {names[i]}{office} [electronically signed on {dates[i]} by {names[i]} in Integrity.gov]'
    if mutation == 'filer_mismatch':
        values['filer_signature'] = values['filer_signature'].replace(filer_name, 'ANOTHER, PERSON')
    elif mutation == 'signer_mismatch':
        values['agency_review'] = values['agency_review'].replace(' by ' + reviewers[0], ' by ANOTHER, PERSON')
    elif mutation == 'reviewer_transaction':
        values['agency_review'] = values['agency_review'].replace(reviewers[0], 'ACME, Purchase 01/02/2026 $1,001')
    elif mutation == 'invalid_date':
        values['agency_review'] = values['agency_review'].replace('06/14/2026', '02/30/2026')
    elif mutation == 'wrong_review_role':
        values['other_review'] = values['other_review'].replace(', Ethics Official', ', Certifying Official')
    elif mutation == 'footer_mismatch':
        values['footer'] = 'ANOTHER, PERSON - Page 1'

    def literal(text):
        return '(' + text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ')Tj'

    statements = ['q', '0 g', '0 G']
    for line in template['lines']:
        y = template['height'] - line['top']
        statements += ['q', '1 w', '.75294 .75294 .75294 RG',
                       f"{line['x0']} {y} m", f"{line['x1']} {y} l", 'S', 'Q']
    for line in template['static_text']:
        statements += ['BT', f"/helv {line['size']} Tf", f"{line['x']} {595-line['baseline']} Td",
                       literal(line['text']), 'ET']
    for role, baseline in template['dynamic_baselines'].items():
        text = values[role]
        if role == 'filer_signature':
            text += ' '  # The observed government writer emits one trailing space.
        x = 806 - pymupdf.get_text_length(text, fontname='helv', fontsize=10) if role == 'footer' else 36
        if mutation == 'cross_boundary' and role == 'agency_review':
            baseline += 22
        if mutation == 'missing_revision' and role == 'revision_date':
            continue
        statements += ['BT', '/helv 10 Tf', f'{x:.5f} {595-baseline} Td', literal(text), 'ET']
    if mutation in {'extra_transaction', 'comments'}:
        text = 'ACME Purchase 01/02/2026 $1,001 - $15,000' if mutation == 'extra_transaction' else 'Additional ethics comments'
        y = 510 if mutation == 'extra_transaction' else 340
        statements += ['BT', '/helv 10 Tf', f'36 {595-y} Td', literal(text), 'ET']
    statements += ['Q']
    stream = ('\n'.join(statements) + '\n').encode('ascii')
    document = pymupdf.open()
    page = document.new_page(width=842, height=595)
    page.insert_font(fontname='helv')
    xref = document.get_new_xref()
    document.update_object(xref, '<<>>')
    document.update_stream(xref, stream)
    page.set_contents(xref)
    document.save(path)
    document.close()
    return {'schema_version': 'oge-278t-cover-identity-context/v1',
            'source_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'filer_name': filer_name, 'agency': agency, 'position_title': position_title}


class NativeCoverRoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'source.pdf'

    def observe(self, context):
        from unison_snapshot.oge_278t_cover_role import observe_native_cover_role
        with pdfplumber.open(self.path) as document:
            return observe_native_cover_role(document.pages[0], 1,
                source_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                cover_identity_context=context)

    def test_different_people_and_reviewers_are_typed_source_roles(self):
        from unison_snapshot.oge_278t_cover_role import replay_native_cover_role
        for name, reviewers in [('EXAMPLE, ADA', None),
                ("O'NEIL-SMITH, ANNA B.", ['SMITH, JANE', 'DOE, JOHN', 'JONES, CASEY'])]:
            with self.subTest(name=name):
                context = native_cover(self.path, filer_name=name, reviewers=reviewers)
                proof = self.observe(context)
                self.assertEqual(proof['status'], 'non_table', proof)
                self.assertFalse(proof['financial_fact_qualification'])
                self.assertFalse(proof['recognizer_started'])
                self.assertEqual(proof['typed_roles']['filer_name']['text'], name)
                self.assertEqual(len(proof['typed_roles']), 8)
                self.assertTrue(replay_native_cover_role(self.path, proof,
                    source_sha256=context['source_sha256'], page_number=1,
                    cover_identity_context=context))

    def test_dynamic_roles_cannot_be_arbitrary_text_or_other_sections(self):
        for mutation in ('filer_mismatch', 'signer_mismatch', 'reviewer_transaction', 'invalid_date',
                         'wrong_review_role', 'footer_mismatch', 'cross_boundary',
                         'missing_revision', 'extra_transaction', 'comments'):
            with self.subTest(mutation=mutation):
                context = native_cover(self.path, mutation=mutation)
                self.assertEqual(self.observe(context)['status'], 'unknown')

    def test_observation_preserves_original_native_words_and_only_known_rules_replay(self):
        from unison_snapshot.oge_278t_page_roles import observe_native_page_role, replay_native_page_role
        context = native_cover(self.path)
        source = self.path.read_bytes()
        with pdfplumber.open(self.path) as document:
            page = document.pages[0]
            before = deepcopy(page.chars), deepcopy(page.lines), page.extract_words()
            proof = observe_native_page_role(page, 1, source_sha256=context['source_sha256'],
                                             cover_identity_context=context)
            self.assertEqual(proof['status'], 'non_table', proof)
            self.assertEqual(before, (page.chars, page.lines, page.extract_words()))
        self.assertEqual(source, self.path.read_bytes())
        self.assertTrue(replay_native_page_role(self.path, proof, source_sha256=context['source_sha256'],
            page_number=1, cover_identity_context=context))
        self.assertFalse(replay_native_page_role(self.path, {**proof, 'rule_version': 'invented'},
            source_sha256=context['source_sha256'], page_number=1, cover_identity_context=context))

    def test_independent_context_and_original_replay_are_required(self):
        from unison_snapshot.oge_278t_cover_role import replay_native_cover_role
        context = native_cover(self.path)
        proof = self.observe(context)
        self.assertEqual(self.observe({**context, 'document_id': 'trace-only',
                                      'source_url': 'https://example.invalid/source.pdf'}), proof)
        for wrong in (None, {**context, 'source_sha256': '0'*64},
                      {**context, 'filer_name': 'ANOTHER, PERSON'},
                      {**context, 'agency': 'Different Agency'},
                      {**context, 'position_title': 'Different Job'}):
            with self.subTest(context=wrong):
                self.assertEqual(self.observe(wrong)['status'], 'unknown')
                self.assertFalse(replay_native_cover_role(self.path, proof,
                    source_sha256=context['source_sha256'], page_number=1,
                    cover_identity_context=wrong))
        wrong = deepcopy(proof)
        wrong['typed_roles']['filer_name']['text'] = 'FORGED, PERSON'
        self.assertFalse(replay_native_cover_role(self.path, wrong,
            source_sha256=context['source_sha256'], page_number=1, cover_identity_context=context))
        self.path.write_bytes(self.path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'different source PDF'):
            replay_native_cover_role(self.path, proof,
                source_sha256=context['source_sha256'], page_number=1, cover_identity_context=context)


if __name__ == '__main__':
    unittest.main()
