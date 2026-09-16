import io
import math
import unittest
import zipfile

import cadquery as cq

from geomeditor.kernel import Document, GeometryError


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.doc.sample('tank')

    def op(self, action, **params):
        self.doc.operate(dict(action=action, revision=self.doc.revision, bodies=['B1'], **params))

    def shape(self):
        return self.doc.body('B1').shape

    def test_water_plane_has_exact_shared_interface_and_conserves_volume(self):
        before = self.shape().Volume()
        self.op('split_plane', origin=[0,0,30], normal=[0,0,1])
        shape = self.shape()
        solids = shape.Solids()
        self.assertEqual(len(solids), 2)
        self.assertAlmostEqual(sum(s.Volume() for s in solids), before, places=5)
        common = set(solids[0].Faces()) & set(solids[1].Faces())
        self.assertEqual(len(common), 1)
        interface = common.pop()
        self.assertAlmostEqual(interface.Center().z, 30, places=8)
        self.assertAlmostEqual(interface.Area(), 120*60, places=5)
        self.assertTrue(all(s.isValid() and s.Shells()[0].Closed() for s in solids))

    def test_face_imprint_keeps_one_solid_and_closed_shell(self):
        before = self.shape().Volume()
        self.op('split_plane', origin=[0,0,30], normal=[0,0,1], scope='surface', faces=['B1:F1'])
        self.assertEqual(len(self.shape().Solids()), 1)
        self.assertEqual(len(self.shape().Faces()), 7)
        self.assertAlmostEqual(self.shape().Volume(), before)
        self.assertEqual(self.doc.scene()['bodies'][0]['topology']['free'], 0)
        self.doc.history('undo')
        self.assertEqual(len(self.shape().Faces()), 6)
        self.doc.history('redo')
        self.assertEqual(len(self.shape().Faces()), 7)

    def test_curved_face_imprint(self):
        self.doc = Document(); self.doc.sample('cylinder')
        face = next(f['id'] for f in self.doc.scene()['faces'] if f['type']=='CYLINDER')
        self.op('split_plane', origin=[0,0,25], normal=[0,0,1], scope='surface', faces=[face])
        self.assertEqual(len(self.shape().Solids()),1)
        self.assertEqual(len(self.shape().Faces()),4)
        self.assertAlmostEqual(self.shape().Volume(), math.pi*30**2*80, places=5)

    def test_nonintersection_is_transactional(self):
        revision = self.doc.revision
        with self.assertRaises(GeometryError):
            self.op('split_plane', origin=[0,0,800], normal=[0,0,1])
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(len(self.shape().Faces()),6)

    def test_stale_selection_rejected(self):
        with self.assertRaises(GeometryError):
            self.doc.operate(dict(action='delete', revision=0, bodies=['B1']))
        self.assertEqual(len(self.doc.bodies),1)

    def test_degenerate_vectors_rejected(self):
        for normal in ([0,0,0], [float('nan'),0,1]):
            with self.assertRaises(GeometryError):
                self.op('split_plane',origin=[0,0,0],normal=normal)
        with self.assertRaises(GeometryError):
            self.op('split_line',origin=[0,0,0],end=[0,0,1],sweep=[0,0,1])

    def test_swept_line_creates_real_face_boundary(self):
        self.op('split_line',origin=[-60,-30,30],end=[-60,30,30],sweep=[1,0,0],scope='surface',faces=['B1:F1'])
        self.assertEqual(len(self.shape().Faces()),7)
        self.assertEqual(len(self.shape().Solids()),1)

    def test_finite_line_not_crossing_face_rejected(self):
        with self.assertRaises(GeometryError):
            self.op('split_line',origin=[-60,-5,30],end=[-60,5,30],sweep=[1,0,0],extend=False,scope='surface',faces=['B1:F1'])
        self.assertEqual(len(self.shape().Faces()),6)

    def test_surface_body_can_be_partitioned(self):
        self.doc = Document(); self.doc.sample('surface')
        self.op('split_plane',origin=[0,0,0],normal=[1,0,0])
        self.assertEqual(len(self.shape().Faces()),2)
        self.assertEqual(len(self.shape().Solids()),0)
        self.assertAlmostEqual(self.shape().Area(),7000)

    def test_step_and_iges_roundtrip_preserve_volume_and_regions(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1])
        for fmt in ('step','iges'):
            with self.subTest(format=fmt):
                raw=self.doc.export_cad(fmt); doc=Document();doc.import_cad(raw,'test.'+fmt)
                shape=doc.bodies[0].shape
                self.assertEqual(len(shape.Solids()),2)
                self.assertTrue(shape.isValid())
                self.assertAlmostEqual(sum(s.Volume() for s in shape.Solids()),576000,places=4)
                self.assertTrue(any(abs(f.Center().z-30)<1e-6 and abs(f.Area()-7200)<1e-4 for f in shape.Faces()))

    def test_step_face_imprint_survives_export(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1],scope='surface',faces=['B1:F1'])
        doc=Document();doc.import_cad(self.doc.export_cad('step'),'test.step')
        self.assertEqual(len(doc.bodies[0].shape.Faces()),7)
        self.assertEqual(len(doc.bodies[0].shape.Solids()),1)

    def test_mixed_solid_and_sheet_import_keeps_sheet(self):
        self.doc.commit([self.doc.make_body(cq.Compound.makeCompound([self.shape(),cq.Face.makePlane(10,10,basePnt=(200,0,0))]),'mixed')],'mixed')
        doc=Document();doc.import_cad(self.doc.export_cad('step'),'mixed.step')
        self.assertEqual(len(doc.bodies[0].shape.Solids()),1)
        self.assertEqual(len(doc.bodies[0].shape.Faces()),7)

    def test_project_retains_shared_topology_and_names(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1])
        raw=self.doc.save_project();doc=Document();doc.load_project(raw)
        self.assertEqual(doc.bodies[0].name,self.doc.bodies[0].name)
        a,b=doc.bodies[0].shape.Solids()
        self.assertEqual(len(set(a.Faces())&set(b.Faces())),1)

    def test_transform_copy_and_undo(self):
        self.op('translate',delta=[200,0,0],copy=True)
        self.assertEqual(len(self.doc.bodies),2)
        self.assertAlmostEqual(self.doc.bodies[1].shape.Center().x,200)
        self.doc.history('undo');self.assertEqual(len(self.doc.bodies),1)
        self.doc.history('redo');self.assertEqual(len(self.doc.bodies),2)
        self.op('rotate',origin=[0,0,0],axis=[0,0,1],angle=90)
        self.assertAlmostEqual(self.shape().BoundingBox().xlen,60)
        self.op('mirror',normal=[0,0,1],origin=[0,0,0])
        self.assertAlmostEqual(self.shape().Center().z,-40)
        self.op('scale',factor=.5)
        self.assertAlmostEqual(self.shape().Volume(),576000/8)

    def test_boolean_operations_have_expected_volumes(self):
        for action,expected in [('union',720000),('difference',144000),('intersection',432000),('partition',720000)]:
            with self.subTest(action=action):
                self.setUp();self.op('translate',delta=[30,0,0],copy=True)
                self.doc.operate(dict(action=action,revision=self.doc.revision,bodies=['B1','B2']))
                self.assertEqual(len(self.doc.bodies),1)
                self.assertAlmostEqual(sum(s.Volume() for s in self.shape().Solids()),expected,places=4)
                if action=='partition':self.assertGreater(len(self.shape().Solids()),1)

    def test_extract_offset_extrude(self):
        self.op('extract',faces=['B1:F6'])
        self.assertEqual(len(self.doc.bodies[1].shape.Solids()),0)
        self.op('offset',faces=['B1:F6'],distance=3)
        self.assertAlmostEqual(self.doc.bodies[-1].shape.Center().z,83)
        self.op('extrude',faces=['B1:F6'],delta=[0,0,10])
        self.assertAlmostEqual(self.doc.bodies[-1].shape.Volume(),72000)

    def test_sew_six_detached_faces_into_closed_solid(self):
        faces=self.shape().Faces()
        self.doc=Document()
        # Independently copy each face: no pre-existing shared edges.
        bodies=[self.doc.make_body(f.copy(),f'face {i}') for i,f in enumerate(faces)]
        self.doc.commit(bodies,'sheets')
        self.doc.operate(dict(action='sew',revision=self.doc.revision,bodies=[b.id for b in bodies],make_solid=True))
        self.assertEqual(len(self.doc.bodies),1)
        self.assertEqual(len(self.doc.bodies[0].shape.Solids()),1)
        self.assertAlmostEqual(self.doc.bodies[0].shape.Volume(),576000)

    def test_patch_closed_planar_loop(self):
        face=self.shape().Faces()[-1]
        ids=[e['id'] for e in self.doc.scene()['edges'] if all(abs(p[2]-80)<1e-6 for p in e['points'])]
        self.op('patch',edges=ids)
        self.assertAlmostEqual(self.doc.bodies[-1].shape.Area(),face.Area())

    def test_unify_removes_imprinted_boundary(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1],scope='surface',faces=['B1:F1'])
        self.op('unify');self.assertEqual(len(self.shape().Faces()),6)

    def test_edge_fillet_and_defeature(self):
        before=self.shape().Volume()
        self.op('fillet',edges=['B1:E1'],size=2)
        self.assertLess(self.shape().Volume(),before)
        faces=[f['id'] for f in self.doc.scene()['faces'] if f['type']=='CYLINDER']
        self.op('defeature',faces=faces)
        self.assertAlmostEqual(self.shape().Volume(),before,places=5)

    def test_chamfer(self):
        self.op('chamfer',edges=['B1:E1'],size=2)
        self.assertLess(self.shape().Volume(),576000)
        self.assertTrue(self.shape().isValid())

    def test_measure_and_periodic_seam(self):
        result=self.doc.measure(['B1:F1','B1:F2'])
        self.assertAlmostEqual(result['distance'],120)
        self.doc=Document();self.doc.sample('cylinder')
        self.assertEqual(self.doc.scene()['bodies'][0]['topology']['free'],0)
        self.assertEqual(self.doc.scene()['bodies'][0]['topology']['seam'],1)

    def test_perpendicular_and_parallel_edge_reference(self):
        edge=next(e['id'] for e in self.doc.scene()['edges'] if abs(e['points'][0][2]-e['points'][-1][2])>79)
        self.op('perpendicular',edge=edge,origin=[0,0,30])
        self.assertEqual(len(self.shape().Solids()),2)
        self.setUp()
        edge=next(e['id'] for e in self.doc.scene()['edges'] if abs(e['points'][0][0]-e['points'][-1][0])>119)
        self.op('parallel',edge=edge,origin=[0,0,30],sweep=[0,1,0])
        self.assertEqual(len(self.shape().Solids()),2)

    def test_split_with_selected_finite_tool_face(self):
        self.doc.commit(self.doc.bodies+[self.doc.make_body(cq.Face.makePlane(200,200,basePnt=(0,0,30)),'tool')],'tool')
        self.op('split_tool',tool='B2:F1')
        self.assertEqual(len(self.shape().Solids()),2)

    def test_explode_does_not_drop_mixed_surfaces(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1])
        self.op('explode')
        self.assertEqual(len(self.doc.bodies),2)
        self.assertAlmostEqual(sum(b.shape.Volume() for b in self.doc.bodies),576000)

    def test_failed_offset_is_atomic(self):
        revision=self.doc.revision
        with self.assertRaises(GeometryError):self.op('offset',faces=['B1:F1'],distance=0)
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(len(self.doc.bodies),1)

    def test_brep_export_preserves_shared_interface(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1])
        shape=cq.Shape.importBrep(io.BytesIO(self.doc.export_cad('brep')))
        a,b=shape.Solids()
        self.assertEqual(len(set(a.Faces())&set(b.Faces())),1)
        self.assertAlmostEqual(a.Volume()+b.Volume(),576000)

    def test_step_iges_have_coincident_interfaces_without_shared_identity(self):
        self.op('split_plane',origin=[0,0,30],normal=[0,0,1])
        for fmt in ('step','iges'):
            doc=Document();doc.import_cad(self.doc.export_cad(fmt),'test.'+fmt)
            a,b=doc.bodies[0].shape.Solids()
            self.assertEqual(len(set(a.Faces())&set(b.Faces())),0)
            self.assertAlmostEqual(a.distance(b),0,places=7)

    def test_three_body_intersection_uses_all_operands(self):
        self.op('translate',delta=[30,0,0],copy=True)
        self.op('translate',delta=[-30,0,0],copy=True)
        self.doc.operate(dict(action='intersection',revision=self.doc.revision,bodies=['B1','B2','B3']))
        self.assertAlmostEqual(self.shape().Volume(),60*60*80,places=5)

    def test_invalid_file_rejected(self):
        with self.assertRaises(GeometryError):self.doc.import_cad(b'not cad','file.stl')
        with self.assertRaises(GeometryError):self.doc.load_project(b'not zip')


if __name__=='__main__':
    unittest.main()
