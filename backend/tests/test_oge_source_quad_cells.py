"""Source quad TSV replay contracts; synthetic render is not OCR accuracy."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from PIL import Image
from test_oge_dual_axis_fields import dual_input,bind_secondary
from unison_snapshot.oge_278t_source_quad_cells import source_quad_cell,observe_saved_source_quad_cells,canonical_sha
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes,_complete_secondary_geometry


class SourceQuadCellTests(unittest.TestCase):
    def test_number_content_crop_keeps_all_interior_ink_and_rejects_frame_contact(self):
        from unison_snapshot.oge_278t_source_quad_cells import _number_content_pixels,NUMBER_CONTENT_MODE
        pixels=np.ones((100,200),dtype='uint8')*255
        pixels[20,20:180]=pixels[79,20:180]=0
        pixels[20:80,20]=pixels[20:80,179]=0
        pixels[30:45,35:38]=0;pixels[30:33,35:47]=0
        pixels[30:45,55:58]=0;pixels[30:33,55:67]=0
        box,crop,proof=_number_content_pixels(pixels,padding=20)
        self.assertTrue(np.array_equal(crop,pixels[box[1]:box[3],box[0]:box[2]]))
        self.assertEqual(sum(p['frame'] for p in proof),1)
        # Even a single interior dot is retained, not dismissed as OCR noise.
        dot=pixels.copy();dot[65,140]=0
        other,kept,_=_number_content_pixels(dot,padding=20)
        self.assertGreaterEqual(other[2],143);self.assertEqual(kept[65-other[1],140-other[0]],0)
        attached=pixels.copy();attached[40,20:50]=0
        with self.assertRaisesRegex(ValueError,'unresolved frame'):_number_content_pixels(attached,padding=20)
        for empty in [np.ones_like(pixels)*255,np.where(pixels<100,0,255).astype('uint8')]:
            if (empty==pixels).all():empty[25:75,25:175]=255
            with self.assertRaises(ValueError):_number_content_pixels(empty,padding=20)
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder),'type')
            with self.assertRaisesRegex(ValueError,'number field'):
                source_quad_cell(render,data['pages'][0],data['pages'][0]['slots'][0],'type',layer='full_source',content_crop_mode=NUMBER_CONTENT_MODE)

    def test_mapped_quad_mode_preserves_bbox_and_rejects_padding_and_proof_tamper(self):
        from unison_snapshot.oge_278t_source_quad_cells import DETECTION_QUAD_MODE
        for overhang in (0,1):
            with self.subTest(overhang=overhang),TemporaryDirectory() as folder:
                source,data,render,saved=self.fixture(Path(folder),'type')
                recipe,pixels=source_quad_cell(render,data['pages'][0],data['pages'][0]['slots'][0],'type',layer='source_text_objects_auxiliary')
                text=saved[0]['cells'][0]['tsv_utf8'];head,row=text.splitlines();parts=row.split('\t')
                # The bottom corners reach the real slanted boundary. Their
                # enclosing world-axis rectangle is deliberately not the proof.
                parts[7]=str(pixels.shape[0]-20-1-4+overhang)
                text=head+'\n'+'\t'.join(parts)+'\n';saved[0]['cells'][0]['tsv_utf8']=text
                saved[0]['cells'][0]['recipe']['tsv_sha256']=hashlib.sha256(text.encode()).hexdigest()
                with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                    old=self.make(source,data,saved,'type')
                    new=observe_saved_source_quad_cells(source,slots_observation=data,field='type',saved_pages=saved,
                        layer='source_text_objects_auxiliary',ocr_engine='saved synthetic TSV',detection_geometry_mode=DETECTION_QUAD_MODE)
                    a=old['pages'][0]['slots'][0]['column_words'][0];b=new['pages'][0]['slots'][0]['column_words'][0]
                    self.assertEqual(a['bbox_pdf_points'],b['bbox_pdf_points'])
                    self.assertEqual(a['text'],b['text'])
                    out=observe_field_shapes([data],secondary_observations=[new],source_pdf_path=source)
                    status=out['pages'][0]['slots'][0]['field_readings']['type']['observations'][1]['geometry_status']
                    self.assertEqual(status,'ambiguous' if overhang else 'source_bound')
                    changed=deepcopy(new);changed['pages'][0]['slots'][0]['column_words'][0]['rectified_bbox_pixels'][3]-=2
                    with self.assertRaisesRegex(ValueError,'replay'):
                        observe_field_shapes([data],secondary_observations=[changed],source_pdf_path=source)

    def fixture(self,root,field='transaction_date',layer='source_text_objects_auxiliary'):
        source=root/'source.pdf';source.write_bytes(b'synthetic source quad PDF')
        data=dual_input();data['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        y,x=np.indices((600,600));full=Image.fromarray(((x+y)%251).astype('uint8'));text=Image.fromarray(((x+2*y)%251).astype('uint8'))
        receipt={'source_sha256':data['source_sha256'],'page_number':2,'resolution':300,
            'source_mediabox_pdf_bottom_left':[0,0,150,150],'source_rotation_degrees':0,
            'pixel_edge_to_source_pdf_affine':[.25,0,0,-.25,0,150]}
        render={'receipt':receipt,'all_image':full,'text_image':text}
        recipe,pixels=source_quad_cell(render,data['pages'][0],data['pages'][0]['slots'][0],field,layer=layer)
        # The whole box is inside the source quad but within the old 1.5pt pad.
        width,height=pixels.shape[1]-40,pixels.shape[0]-40
        token='06/01/2026' if field=='transaction_date' else ('$50,001 - $100,000' if field=='amount' else ('50' if field=='printed_number' else 'purchase'))
        tsv=('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
             f'5\t1\t1\t1\t1\t1\t{20+width-8}\t{20+height//2}\t6\t4\t95\t{token}\n')
        recipe.update(physical_index=0,column={'transaction_date':3,'type':2,'printed_number':0,'amount':4}[field],tsv_sha256=hashlib.sha256(tsv.encode()).hexdigest())
        saved=[{'page_number':2,'render_receipt':receipt,'cells':[{'slot_index':1,'recipe':recipe,'tsv_utf8':tsv}]}]
        return source,data,render,saved

    def make(self,source,data,saved,field='transaction_date',layer='source_text_objects_auxiliary'):
        return observe_saved_source_quad_cells(source,slots_observation=data,field=field,saved_pages=saved,layer=layer,ocr_engine='saved synthetic TSV')

    def test_quad_date_and_type_normal_ingestion_do_not_apply_old_column_padding(self):
        for field in ('type','transaction_date','amount'):
            with self.subTest(field=field),TemporaryDirectory() as folder:
                source,data,render,saved=self.fixture(Path(folder),field)
                before=deepcopy(data)
                with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                    reading=self.make(source,data,saved,field)
                    out=observe_field_shapes([data],secondary_observations=[reading],source_pdf_path=source)
                read=out['pages'][0]['slots'][0]['field_readings'][field]['observations'][1]
                self.assertEqual(read['geometry_status'],'source_bound')
                self.assertTrue(_complete_secondary_geometry(read,field=field,slot=data['pages'][0]['slots'][0],page=data['pages'][0]))
                self.assertIn('auxiliary',read['source_quad_cell']['coverage'])
                self.assertEqual(data,before)
                legacy=bind_secondary(data,field,reading['pages'][0]['slots'][0]['column_raw_ocr_text'])
                key='column_words' if field=='type' else 'column_detections'
                legacy['pages'][0]['slots'][0][key]=deepcopy(reading['pages'][0]['slots'][0][key])
                out=observe_field_shapes([data],secondary_observations=[legacy])
                self.assertEqual(out['pages'][0]['slots'][0]['field_readings'][field]['observations'][1]['geometry_status'],'ambiguous')

    def test_modified_source_quad_crop_matrix_layer_tsv_and_primary_are_rejected(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder))
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                good=self.make(source,data,saved)
                for label in ('quad','matrix','pixels','tsv','layer','render'):
                    with self.subTest(label=label):
                        bad=deepcopy(saved)
                        kwargs={}
                        if label=='quad':bad[0]['cells'][0]['recipe']['quad_pdf_points'][0][0]+=1
                        if label=='matrix':bad[0]['cells'][0]['recipe']['source_pixel_to_cell_matrix'][0][2]+=1
                        if label=='pixels':bad[0]['cells'][0]['recipe']['source_crop_pixel_sha256']='0'*64
                        if label=='tsv':bad[0]['cells'][0]['tsv_utf8']+='changed'
                        if label=='render':bad[0]['render_receipt']['pixel_edge_to_source_pdf_affine'][0]+=.01
                        if label=='layer':kwargs['layer']='full_source'
                        with self.assertRaises(ValueError):self.make(source,data,bad,**kwargs)
                changed=deepcopy(data);changed['pages'][0]['slots'][0]['words'][1]['text']='OTHER'
                from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
                p=changed['pages'][0];p['slots'][0].update(_observe_slot_columns(p['slots'][0]['words'],p['column_edges_pdf_points'],page=p))
                with self.assertRaisesRegex(ValueError,'fingerprint'):
                    observe_field_shapes([changed],secondary_observations=[good],source_pdf_path=source)
                wrong=Path(folder)/'wrong.pdf';wrong.write_bytes(b'other source')
                with self.assertRaisesRegex(ValueError,'SHA'):
                    observe_field_shapes([data],secondary_observations=[good],source_pdf_path=wrong)
                with self.assertRaisesRegex(ValueError,'controlled original'):
                    observe_field_shapes([data],secondary_observations=[good])

    def test_secondary_detections_cannot_be_modified_or_escape_actual_quad(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder))
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                reading=self.make(source,data,saved)
                changed=deepcopy(reading);changed['pages'][0]['slots'][0]['column_detections'][0]['bbox_pdf_points'][2]+=10
                with self.assertRaisesRegex(ValueError,'replay'):
                    observe_field_shapes([data],secondary_observations=[changed],source_pdf_path=source)
                # A genuine saved TSV extending into the padding replays, but
                # normal geometry marks it ambiguous instead of granting proof.
                bad=deepcopy(saved);text=bad[0]['cells'][0]['tsv_utf8'];parts=text.splitlines()[1].split('\t');parts[8]='20'
                text=text.splitlines()[0]+'\n'+'\t'.join(parts)+'\n'
                bad[0]['cells'][0]['tsv_utf8']=text;bad[0]['cells'][0]['recipe']['tsv_sha256']=hashlib.sha256(text.encode()).hexdigest()
                reading=self.make(source,data,bad)
                out=observe_field_shapes([data],secondary_observations=[reading],source_pdf_path=source)
                self.assertEqual(out['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations'][1]['geometry_status'],'ambiguous')

    def test_same_engine_full_and_auxiliary_layers_remain_distinct_observations(self):
        with TemporaryDirectory() as folder:
            source,data,render,aux=self.fixture(Path(folder))
            _,_,_,full=self.fixture(Path(folder),layer='full_source')
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                a=self.make(source,data,aux)
                b=self.make(source,data,full,layer='full_source')
                out=observe_field_shapes([data],secondary_observations=[a,b],source_pdf_path=source)
            reads=out['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations']
            self.assertEqual(len(reads),3)
            self.assertEqual({r['source_quad_cell']['render_layer'] for r in reads[1:]},{'full_source','source_text_objects_auxiliary'})

    def test_normal_saved_materializer_replays_source_quad_reference(self):
        import json
        from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
        from unison_snapshot.whitehouse_278t import materialize_saved_whitehouse_278t,DUAL_AXIS_MODE
        with TemporaryDirectory() as folder:
            root=Path(folder);source,data,render,saved=self.fixture(root)
            case=SavedWhiteHouseTests();inputs,_=case.fixture(root)
            original=Path(inputs['archived_pdf_path'])
            data['source_sha256']=hashlib.sha256(original.read_bytes()).hexdigest()
            render['receipt']['source_sha256']=data['source_sha256'];saved[0]['render_receipt']=deepcopy(render['receipt'])
            inputs['geometry_mode']=DUAL_AXIS_MODE
            with case.context(data),patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                reading=self.make(original,data,saved)
                target=root/'secondary.json';raw=json.dumps([reading]).encode();target.write_bytes(raw)
                inputs.update(secondary_observations_path=str(target),secondary_observations_sha256=hashlib.sha256(raw).hexdigest())
                result=materialize_saved_whitehouse_278t(inputs)
            self.assertIsNotNone(result['content_observations'])
            observations=result['content_observations']['field_observations']['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations']
            self.assertEqual(observations[1]['read_mode'],'source_quad_cells/v1')
            target.write_bytes(b'[]')
            from unison_snapshot.oge import OgeCatalogError
            with self.assertRaisesRegex(OgeCatalogError,'bytes differ'):
                materialize_saved_whitehouse_278t(inputs)

    def test_full_source_layer_is_distinct_and_render_is_shared_per_page(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder),layer='full_source')
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render) as rendering:
                reading=self.make(source,data,saved,layer='full_source')
                out=observe_field_shapes([data],secondary_observations=[reading,dict(reading,ocr_engine='second saved engine')],source_pdf_path=source)
                self.assertEqual(rendering.call_count,2) # producer once, validator once for both reads
            self.assertEqual(out['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations'][1]['source_quad_cell']['coverage'],'field_only_not_page_coverage')

if __name__=='__main__':unittest.main()
