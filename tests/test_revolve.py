import math
import unittest
import cadquery as cq
from geomeditor.kernel import Document, GeometryError


class RevolveTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.doc.commit([self.doc.make_body(cq.Workplane('XY').box(20,20,20).val(), 'box')], 'fixture')
        for point in ([5,0,-15],[5,0,15],[0,0,-1],[0,0,1]):
            self.op('node_create', point=point)
        self.op('line_create', nodes=['N1','N2'])
        self.params = dict(source='L1', axis_mode='vector', axis_origin=[0,0,0], axis_direction=[0,0,1], angle=360)

    def op(self, action, **kw):
        self.doc.operate(dict(action=action, revision=self.doc.revision, **kw))

    def split(self, **kw):
        self.op('split_revolve', **dict(self.params, bodies=['B1'], **kw))

    def test_cylinder_partitions_solid_with_shared_boundary(self):
        self.split()
        shape=self.doc.body('B1').shape
        self.assertEqual(len(shape.Solids()),2)
        self.assertAlmostEqual(shape.Volume(),8000,places=6)
        self.assertAlmostEqual(min(s.Volume() for s in shape.Solids()),math.pi*25*20,places=6)
        self.assertGreater(self.doc.scene()['bodies'][0]['topology']['shared'],0)

    def test_two_node_axis_matches_vector_axis(self):
        self.params.update(axis_mode='nodes',axis_nodes=['N3','N4'])
        self.split()
        self.assertEqual(len(self.doc.body('B1').shape.Solids()),2)
        self.assertAlmostEqual(self.doc.body('B1').shape.Volume(),8000,places=6)

    def test_imprint_and_step_roundtrip(self):
        top=next(f['id'] for f in self.doc.scene()['faces'] if abs(self.doc.entity(f['id'],'F')[1].Center().z-10)<1e-6)
        self.split(scope='surface',faces=[top])
        shape=self.doc.body('B1').shape
        self.assertEqual(len(shape.Faces()),7)
        self.assertEqual(len(shape.Solids()),1)
        other=Document();other.import_cad(self.doc.export_cad('step'),'result.step')
        self.assertEqual(len(other.bodies[0].shape.Faces()),7)
        self.assertAlmostEqual(other.bodies[0].shape.Volume(),8000,places=5)

    def test_cone_and_planar_revolution(self):
        for a,b,area in [([0,0,0],[5,0,10],math.pi*5*math.sqrt(125)),([2,0,0],[5,0,0],math.pi*21)]:
            with self.subTest(a=a):
                self.op('node_move',node='N1',point=a);self.op('node_move',node='N2',point=b)
                self.assertAlmostEqual(self.doc.revolved_tool(self.params).Area(),area,places=6)

    def test_partial_angle_start_and_reverse(self):
        for angle,start,sign in [(90,0,1),(-90,0,-1),(90,90,1)]:
            tool=self.doc.revolved_tool(dict(self.params,angle=angle,start_angle=start))
            self.assertAlmostEqual(tool.Area(),5*math.pi/2*30,places=6)
            self.assertEqual(1 if tool.Center().y>0 else -1,sign)
            self.assertEqual(1 if tool.Center().x>0 else -1, -1 if start==90 else 1)

    def test_cad_edge_source_and_arbitrary_axis(self):
        edge=cq.Edge.makeLine((5,0,-15),(5,0,15))
        self.doc.commit(self.doc.bodies+[self.doc.make_body(edge,'source')],'edge')
        self.assertAlmostEqual(self.doc.revolved_tool(dict(self.params,source='B2:E1')).Area(),300*math.pi,places=6)
        self.op('node_move',node='N1',point=[-15,25,30]);self.op('node_move',node='N2',point=[15,25,30])
        tool=self.doc.revolved_tool(dict(self.params,axis_origin=[0,20,30],axis_direction=[1,0,0]))
        self.assertAlmostEqual(tool.Area(),300*math.pi,places=6)
        self.assertAlmostEqual(tool.Center().y,20,places=6)
        self.assertAlmostEqual(tool.Center().z,30,places=6)

    def test_invalid_inputs_are_atomic(self):
        cases=[dict(angle=0),dict(angle=361),dict(angle=float('nan')),dict(axis_direction=[0,0,0]),dict(axis_mode='nodes',axis_nodes=['N3','N3']),dict(source='L999'),dict(axis_mode='nodes',axis_nodes=['N3','N999'])]
        for case in cases:
            with self.subTest(case=case):
                rev=self.doc.revision
                with self.assertRaises((GeometryError,ValueError)):self.split(**case)
                self.assertEqual(self.doc.revision,rev)
                self.assertEqual(len(self.doc.body('B1').shape.Solids()),1)

    def test_axis_line_and_nonintersection_rejected(self):
        self.op('node_move',node='N1',point=[0,0,-15]);self.op('node_move',node='N2',point=[0,0,15])
        with self.assertRaises(GeometryError):self.split()
        self.op('node_move',node='N1',point=[50,0,-15]);self.op('node_move',node='N2',point=[50,0,15])
        with self.assertRaises(GeometryError):self.split()

    def test_independent_surface_is_partitioned(self):
        face=cq.Face.makePlane(20,20,basePnt=(0,0,0),dir=(0,0,1))
        self.doc.commit([self.doc.make_body(face,'sheet')],'sheet')
        self.op('split_revolve',**dict(self.params,bodies=['B2'],scope='surface',faces=['B2:F1']))
        shape=self.doc.body('B2').shape
        self.assertEqual(len(shape.Faces()),2)
        self.assertAlmostEqual(shape.Area(),400,places=6)

    def test_partial_revolution_actually_splits(self):
        self.op('node_move',node='N1',point=[0,0,0]);self.op('node_move',node='N2',point=[30,0,0])
        self.params.update(angle=180,start_angle=-90)
        self.split()
        self.assertEqual(len(self.doc.body('B1').shape.Solids()),1)
        self.assertGreater(len(self.doc.body('B1').shape.Faces()),6)
        self.assertAlmostEqual(self.doc.body('B1').shape.Volume(),8000,places=6)

    def test_undo_redo_project(self):
        self.split();self.op('undo')
        self.assertEqual(len(self.doc.body('B1').shape.Solids()),1)
        self.op('redo')
        other=Document();other.load_project(self.doc.save_project())
        self.assertEqual(len(other.body('B1').shape.Solids()),2)
        self.assertEqual(len(other.lines),1)

if __name__=='__main__':unittest.main()
