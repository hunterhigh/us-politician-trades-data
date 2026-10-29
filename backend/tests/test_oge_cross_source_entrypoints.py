"""Root-context wiring contracts; actual source qualification has separate tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot import oge_cross_source_replacements as cross
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t import saved_ocr_source_roots, _saved_source_path


class CrossSourceRootTests(unittest.TestCase):
    def test_source_application_preserves_existing_market_without_early_publication(self):
        from test_oge_whitehouse_replacements import cross_fixture, mocks, DOCUMENT_ID
        from test_market_store import row
        with tempfile.TemporaryDirectory() as folder:
            data=cross_fixture(Path(folder))
            market=row('MSFT')
            for point,day in zip(market['price_history'],['2019-09-17','2019-09-18']):
                point['date']=day
            data['wh_inputs']['base']['security_market_data']=[market]
            with mocks(data), patch('unison_snapshot.builder.build',side_effect=AssertionError('Publication belongs to a later stage')):
                candidate,_,_=cross._whitehouse_candidate(data['wh_inputs'])
                result,proof=cross.apply_cross_source_native_replacements(candidate,
                    {DOCUMENT_ID:data['inputs']},data['wh_inputs'],data['evidence'],data['review'])
            self.assertEqual(len(proof['applications']),1)
            self.assertEqual(result['security_market_data'],[market])

    def check_paths(self, evidence, review):
        self.assertEqual(_saved_source_path('whitehouse/saved/input.json', evidence=True),
                         evidence.resolve()/'whitehouse/saved/input.json')
        self.assertEqual(_saved_source_path('whitehouse/saved/input.json'),
                         review.resolve()/'whitehouse/saved/input.json')

    def test_all_public_entrypoints_bind_relative_source_roots_without_external_context(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = Path(folder)/'evidence', Path(folder)/'review'
            context = {'schema_version': cross.SCHEMA, 'wh_inputs': {}, 'saved_inputs': {'source': {}}}
            proof = {'schema_version': 'oge-whitehouse-native-replacement-application/v1', 'context': context}
            def family(inputs):
                self.check_paths(evidence,review)
                return {'candidate':True},{},{}
            def apply(*args):
                self.check_paths(evidence,review)
                return {'candidate':True},proof
            with saved_ocr_source_roots(None,None), \
                    patch.object(cross,'_whitehouse_candidate',side_effect=family), \
                    patch.object(cross,'_apply_cross_source_native_replacements',side_effect=apply):
                calls = [lambda: cross.apply_cross_source_native_replacements({}, {'source':{}}, {}, evidence,review),
                         lambda: cross.verify_cross_source_native_applications(proof,evidence,review),
                         lambda: cross.replay_cross_source_context(context,evidence,review)]
                for call in calls:
                    self.assertEqual(call(),({'candidate':True},proof))
                    with self.assertRaisesRegex(OgeCatalogError,'explicit evidence/review roots'):
                        _saved_source_path('whitehouse/saved/input.json',evidence=True)

    def test_nested_context_is_restored_after_success_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);outer_e,outer_r=root/'outer-e',root/'outer-r'
            inner_e,inner_r=root/'inner-e',root/'inner-r'
            def apply(*args):
                self.check_paths(inner_e,inner_r)
                raise OgeCatalogError('controlled failure')
            with saved_ocr_source_roots(outer_e,outer_r), \
                    patch.object(cross,'_apply_cross_source_native_replacements',side_effect=apply):
                with self.assertRaisesRegex(OgeCatalogError,'controlled failure'):
                    cross.apply_cross_source_native_replacements({}, {'source':{}}, {},inner_e,inner_r)
                self.check_paths(outer_e,outer_r)

    def test_binding_roots_keeps_path_escape_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def apply(*args):
                _saved_source_path('../outside.json',evidence=True)
            with patch.object(cross,'_apply_cross_source_native_replacements',side_effect=apply):
                with self.assertRaisesRegex(OgeCatalogError,'escapes controlled root'):
                    cross.apply_cross_source_native_replacements({}, {'source':{}}, {},root/'e',root/'r')
