"""Direct saved quad transport only; synthetic renders are not source-reading gold."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_oge_source_quad_cells as source_quad_tests
from test_oge_saved_observations_cli import normal_fixture
from unison_snapshot.oge_278t_fields import LEGACY_AMOUNT_INTERPRETATION_MODE
from unison_snapshot.oge_reports import PARSER_VERSION
from unison_snapshot.oge_saved_observations import (
    _request, load_saved_oge_observations, prepare_saved_oge_observations,
)
from unison_snapshot.oge_transaction_adapter import (
    _source_content_relations, materialize_oge_278t_observations,
)


class StopAfterReplay(RuntimeError):
    pass


class SavedSourceQuadContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, field='type'):
        helper = source_quad_tests.SourceQuadCellTests()
        source, primary, render, pages = helper.fixture(self.root, field, layer='full_source')
        render['receipt']['annotation_count_excluded_from_all_renders'] = 0
        with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects', return_value=render):
            reading = helper.make(source, primary, pages, field=field, layer='full_source')
        return source, primary, render, reading

    def test_prepare_and_load_replay_type_amount_and_printed_number_but_keep_portable_paths_out(self):
        for field in ('type', 'amount', 'printed_number'):
            with self.subTest(field=field):
                source, primary, render, reading = self.fixture(field)
                before = deepcopy((primary, reading))
                document = 'a'*32
                metadata = {'document_id': document, 'sha256': primary['source_sha256'],
                    'document_url': 'https://www.oge.gov/source.pdf', 'filer_name':'Example',
                    'agency':'Agency', 'position_title':'Position'}
                extraction = {'document_id': document, 'source_sha256': metadata['sha256'],
                    'parser_version': PARSER_VERSION, 'source_url': metadata['document_url'],
                    'catalog_filer_name':'Example', 'agency':'Agency', 'position_title':'Position'}
                with patch('unison_snapshot.oge_saved_observations._source',
                           return_value=(metadata,source,2)), patch(
                           'unison_snapshot.pdf_text_objects.render_pdf_text_objects', return_value=render):
                    package = prepare_saved_oge_observations(evidence_root=self.root,
                        metadata_path=self.root/'metadata.json', observations=[primary],
                        secondary_observations=[reading])
                    target=self.root/'saved.json'; target.write_text(json.dumps(package))
                    loaded=load_saved_oge_observations(target,evidence_root=self.root,extractions=[extraction])
                self.assertEqual(set(package[document]),
                    {'observations','document_page_count','secondary_observations'})
                self.assertNotIn(str(source),json.dumps(package))
                self.assertEqual(loaded[document],{**package[document],'archived_pdf_path':str(source)})
                self.assertEqual(before,(primary,reading))

    def test_unsupported_quad_policies_and_excluded_annotations_fail_before_source_replay(self):
        source, primary, _, reading = self.fixture()
        variants=[]
        for schema, field in (('date','transaction_date'),('description','description')):
            bad=deepcopy(reading); bad['schema_version']=f'oge-278t-{schema}-column-observation/v1'
            bad['source_quad_input']['field']=field; variants.append(bad)
        bad=deepcopy(reading);bad['source_quad_input']['field']='amount';variants.append(bad)
        bad=deepcopy(reading);bad['source_quad_input']['layer']='source_text_objects_auxiliary';variants.append(bad)
        for count in (1,False,None,'0'):
            bad=deepcopy(reading)
            bad['source_quad_input']['saved_pages'][0]['render_receipt']['annotation_count_excluded_from_all_renders']=count
            variants.append(bad)
        with patch('unison_snapshot.oge_278t_source_quad_cells.validate_source_quad_reading',
                   side_effect=AssertionError('preflight must reject before replay')):
            for bad in variants:
                with self.subTest(reading=bad['source_quad_input']):
                    with self.assertRaises(ValueError):
                        _request([primary],[bad],2,primary['source_sha256'],source_pdf_path=source)
            with self.assertRaisesRegex(ValueError,'controlled original'):
                _request([primary],[reading],2,primary['source_sha256'])
            with self.assertRaisesRegex(ValueError,'legacy'):
                _request([primary],[reading],2,primary['source_sha256'],source_pdf_path=source,
                    field_interpretation_mode=LEGACY_AMOUNT_INTERPRETATION_MODE)

    def test_no_quad_request_keeps_old_keys_and_requires_no_source_path(self):
        _, primary, _, _ = self.fixture()
        result=_request([primary],None,2,primary['source_sha256'])
        self.assertEqual(result,{'observations':[primary],'document_page_count':2})

    def test_adapter_materialization_and_relation_replay_pass_same_controlled_source_twice(self):
        # Stop immediately on the second field call: this tests forwarding,
        # not synthetic qualification, source coverage or financial acceptance.
        _, _, _, reading=self.fixture()
        (self.root/'normal').mkdir()
        data=normal_fixture(self.root/'normal')
        reading['source_sha256']=data['sha']
        request=data['request']
        first={'page_role_conflicts':[{'page_number':2}]}
        for lane in ('materialize','relations'):
            with self.subTest(lane=lane), patch(
                    'unison_snapshot.oge_278t_fields.observe_content_rows',
                    side_effect=[deepcopy(first),StopAfterReplay()]) as replay, patch(
                    'unison_snapshot.oge_transaction_adapter._audit_content_source',return_value={}):
                with self.assertRaises(StopAfterReplay):
                    if lane=='materialize':
                        materialize_oge_278t_observations(data['old'],data['record'],
                            observations=request['observations'],document_page_count=2,
                            secondary_observations=[reading],archived_pdf_path=str(data['source']))
                    else:
                        _source_content_relations(data['old'],request['observations'],
                            document_page_count=2,secondary_field_observations=[reading],
                            archived_pdf_path=str(data['source']))
                self.assertEqual(replay.call_count,2)
                self.assertEqual([call.kwargs['source_pdf_path'] for call in replay.call_args_list],
                    [str(data['source'])]*2)
                self.assertEqual(replay.call_args_list[1].kwargs['source_role_conflict_pages'],{2})


if __name__=='__main__':
    unittest.main()
