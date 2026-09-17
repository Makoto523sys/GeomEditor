import bisect
import math
import unittest

import cadquery as cq

from geomeditor.kernel import Document, GeometryError


class EdgeNodeTests(unittest.TestCase):
    def setUp(self):
        self.doc=Document()
        self.edge=cq.Edge.makeLine(cq.Vector(10,20,30),cq.Vector(110,20,30))
        self.doc.commit([self.doc.make_body(self.edge,'edge')],'fixture')

    def op(self, action, **kw):
        self.doc.operate(dict(action=action,revision=self.doc.revision,edge='B1:E1',**kw))

    def use(self, edge):
        self.doc=Document();self.edge=edge
        self.doc.commit([self.doc.make_body(edge,'edge')],'fixture')

    def test_fraction_and_reverse_match_displayed_direction(self):
        self.op('node_on_edge',fraction=.1)
        self.assertEqual(self.doc.nodes[0]['point'],(20.,20.,30.))
        self.op('node_on_edge',fraction=.1,reverse=True)
        self.assertEqual(self.doc.nodes[1]['point'],(100.,20.,30.))
        self.op('node_on_edge',fraction=0)
        self.op('node_on_edge',fraction=1)
        scene=self.doc.scene()
        self.assertEqual(scene['edges'][0]['start'],self.doc.nodes[2]['point'])
        self.assertEqual(scene['edges'][0]['end'],self.doc.nodes[3]['point'])

    def test_equal_division_includes_endpoints_and_is_one_undo_step(self):
        self.op('nodes_divide_edge',divisions=4)
        self.assertEqual([n['point'][0] for n in self.doc.nodes],[10,35,60,85,110])
        self.op('undo');self.assertFalse(self.doc.nodes)
        self.op('redo');self.assertEqual(len(self.doc.nodes),5)
        self.op('node_create',point=[0,0,0]);self.assertEqual(self.doc.nodes[-1]['id'],'N6')

    def test_arc_equal_lengths_and_reversed_order(self):
        self.use(cq.Edge.makeCircle(20,angle1=0,angle2=90))
        self.op('nodes_divide_edge',divisions=4,reverse=True)
        for i,n in enumerate(self.doc.nodes):
            a=(1-i/4)*math.pi/2
            self.assertAlmostEqual(n['point'][0],20*math.cos(a),places=6)
            self.assertAlmostEqual(n['point'][1],20*math.sin(a),places=6)

    def test_nonuniform_bezier_uses_arc_length_not_parameter(self):
        controls=[(0,0,0),(1,0,0),(3,90,0),(100,100,0)]
        self.use(cq.Edge.makeBezier([cq.Vector(*p) for p in controls]))
        self.op('nodes_divide_edge',divisions=5)
        def point(t):
            weights=[(1-t)**3,3*(1-t)**2*t,3*(1-t)*t*t,t**3]
            return tuple(sum(w*p[j] for w,p in zip(weights,controls)) for j in range(3))
        # Independent dense evaluation of the cubic polynomial and its accumulated lengths.
        samples=[point(i/20000) for i in range(20001)]
        lengths=[0.]
        for a,b in zip(samples,samples[1:]):lengths.append(lengths[-1]+math.dist(a,b))
        for i,node in enumerate(self.doc.nodes[1:-1],1):
            target=lengths[-1]*i/5;k=bisect.bisect_left(lengths,target)
            ratio=(target-lengths[k-1])/(lengths[k]-lengths[k-1])
            expected=tuple(a+ratio*(b-a) for a,b in zip(samples[k-1],samples[k]))
            self.assertLess(math.dist(node['point'],expected),1e-4)
        self.assertGreater(math.dist(self.doc.nodes[1]['point'],point(.2)),1)

    def test_closed_edge_creates_n_plus_one_including_duplicate_seam(self):
        self.use(cq.Edge.makeCircle(10))
        self.op('nodes_divide_edge',divisions=4)
        self.assertEqual(len(self.doc.nodes),5)
        self.assertLess(math.dist(self.doc.nodes[0]['point'],self.doc.nodes[-1]['point']),1e-7)
        self.assertIn('同じ位置',self.doc.message)
        self.assertTrue(self.doc.scene()['edges'][0]['closed'])

    def test_invalid_counts_and_fractions_do_not_partially_create(self):
        revision=self.doc.revision
        for n in [0,-1,2.5,1001,float('nan')]:
            with self.assertRaises(GeometryError):self.op('nodes_divide_edge',divisions=n)
        for r in [-.1,1.1,float('inf')]:
            with self.assertRaises(GeometryError):self.op('node_on_edge',fraction=r)
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(self.doc.next_node,1)
        self.assertFalse(self.doc.nodes)

    def test_division_one_and_project_roundtrip(self):
        self.op('nodes_divide_edge',divisions=1)
        other=Document();other.load_project(self.doc.save_project())
        self.assertEqual(len(other.nodes),2)
        self.assertEqual(tuple(other.nodes[0]['point']),self.doc.nodes[0]['point'])
        self.assertEqual(other.next_node,3)

    def test_edge_nodes_connect_to_line_and_imprint_face(self):
        self.doc=Document();self.doc.sample('tank')
        # Choose the two vertical edges of the x=-60 face, independent of edge ordering.
        edges=[e for e in self.doc.scene()['edges'] if abs(e['start'][0]+60)<1e-6 and abs(e['end'][0]+60)<1e-6 and abs(e['end'][2]-e['start'][2])>79]
        self.assertEqual(len(edges),2)
        for edge in edges:
            r=(30-edge['start'][2])/(edge['end'][2]-edge['start'][2])
            self.doc.operate(dict(action='node_on_edge',revision=self.doc.revision,edge=edge['id'],fraction=r))
        self.op('line_create',nodes=['N1','N2'])
        self.op('split_reference',line='L1',bodies=['B1'],faces=['B1:F1'],scope='surface',sweep=[1,0,0],extend=False)
        shape=self.doc.body('B1').shape
        self.assertEqual(len(shape.Faces()),7)
        self.assertEqual(len(shape.Solids()),1)
        self.assertAlmostEqual(shape.Volume(),576000)

    def test_stale_edge_request_is_rejected(self):
        old=self.doc.revision
        self.op('nodes_divide_edge',divisions=2)
        with self.assertRaises(GeometryError):
            self.doc.operate(dict(action='node_on_edge',revision=old,edge='B1:E1',fraction=.1))
        self.assertEqual(len(self.doc.nodes),3)

if __name__=='__main__':unittest.main()
