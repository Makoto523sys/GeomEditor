import math
import unittest
import cadquery as cq
from geomeditor.kernel import Document, GeometryError


class PerpendicularTests(unittest.TestCase):
    def setup_edge(self, edge, point):
        self.doc=Document()
        self.doc.commit([self.doc.make_body(edge,'edge')],'fixture')
        self.op('node_create',point=point)

    def op(self,action,**kw):
        self.doc.operate(dict(action=action,revision=self.doc.revision,**kw))

    def perpendicular(self):
        self.op('perpendicular_line',node='N1',edge='B1:E1')

    def test_straight_edge_creates_foot_and_reusable_line(self):
        self.setup_edge(cq.Edge.makeLine((0,0,0),(10,0,0)),[3,4,5])
        self.perpendicular()
        self.assertEqual(self.doc.nodes[1]['point'],(3.,0.,0.))
        self.assertEqual(self.doc.lines[0]['nodes'],['N1','N2'])
        self.assertAlmostEqual(self.doc.scene()['lines'][0]['length'],math.sqrt(41))

    def test_transformed_edge_uses_world_coordinates(self):
        edge=cq.Edge.makeLine((0,0,0),(10,0,0)).located(cq.Location(cq.Vector(20,30,40),cq.Vector(0,0,1),90))
        self.setup_edge(edge,[25,33,40]);self.perpendicular()
        self.assertLess(math.dist(self.doc.nodes[1]['point'],[20,33,40]),1e-7)

    def test_outside_segment_does_not_create_slanted_endpoint_line(self):
        self.setup_edge(cq.Edge.makeLine((0,0,0),(10,0,0)),[15,3,0])
        revision=self.doc.revision
        with self.assertRaises(GeometryError):self.perpendicular()
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(len(self.doc.nodes),1)
        self.assertFalse(self.doc.lines)

    def test_endpoint_is_allowed_when_actually_perpendicular(self):
        self.setup_edge(cq.Edge.makeLine((0,0,0),(10,0,0)),[0,3,0]);self.perpendicular()
        self.assertLess(math.dist(self.doc.nodes[1]['point'],[0,0,0]),1e-7)

    def test_arc_foot_is_nearest_and_tangent_orthogonal(self):
        self.setup_edge(cq.Edge.makeCircle(10,angle1=0,angle2=90),[15,15,2]);self.perpendicular()
        q=self.doc.nodes[1]['point']
        self.assertLess(math.dist(q,[10/math.sqrt(2),10/math.sqrt(2),0]),1e-7)
        tangent=(-q[1],q[0],0)
        self.assertAlmostEqual(sum((a-b)*t for a,b,t in zip([15,15,2],q,tangent)),0,places=7)

    def test_circle_axis_ambiguity_is_rejected(self):
        self.setup_edge(cq.Edge.makeCircle(10),[0,0,3])
        with self.assertRaises(GeometryError):self.perpendicular()
        self.assertEqual(len(self.doc.nodes),1)

    def test_source_on_edge_does_not_create_zero_line(self):
        self.setup_edge(cq.Edge.makeLine((0,0,0),(10,0,0)),[3,0,0])
        with self.assertRaises(GeometryError):self.perpendicular()
        self.assertFalse(self.doc.lines)

    def test_smooth_bezier_foot_lies_on_curve_and_is_perpendicular(self):
        edge=cq.Edge.makeBezier([cq.Vector(0,0,0),cq.Vector(2,5,0),cq.Vector(8,5,0),cq.Vector(10,0,0)])
        self.setup_edge(edge,[5,10,0]);self.perpendicular()
        self.assertLess(math.dist(self.doc.nodes[1]['point'],[5,3.75,0]),1e-6)

    def test_undo_redo_and_project_restore_preserve_pair(self):
        self.setup_edge(cq.Edge.makeLine((0,0,0),(10,0,0)),[3,4,0]);self.perpendicular()
        self.op('undo');self.assertEqual(len(self.doc.nodes),1);self.assertFalse(self.doc.lines)
        self.op('redo');self.assertEqual(len(self.doc.nodes),2)
        other=Document();other.load_project(self.doc.save_project())
        self.assertEqual(other.lines,self.doc.lines)
        self.assertEqual(other.scene()['lines'][0]['length'],4)

    def test_perpendicular_line_imprints_real_evaluation_boundary(self):
        self.doc=Document();self.doc.sample('tank')
        self.op('node_create',point=[-60,-30,30])
        edge=next(e for e in self.doc.scene()['edges'] if abs(e['start'][0]+60)<1e-6 and abs(e['start'][1]-30)<1e-6 and abs(e['end'][1]-30)<1e-6 and abs(e['start'][2]-e['end'][2])>79)
        self.op('perpendicular_line',node='N1',edge=edge['id'])
        self.assertEqual(self.doc.nodes[1]['point'],(-60.,30.,30.))
        self.op('split_reference',line='L1',bodies=['B1'],faces=['B1:F1'],scope='surface',sweep=[1,0,0],extend=False)
        shape=self.doc.body('B1').shape
        self.assertEqual(len(shape.Solids()),1);self.assertEqual(len(shape.Faces()),7)
        self.assertAlmostEqual(shape.Volume(),576000)
        other=Document();other.import_cad(self.doc.export_cad('step'),'result.step')
        self.assertEqual(len(other.bodies[0].shape.Faces()),7)

if __name__=='__main__':unittest.main()
