import io
import json
import unittest
import zipfile

from geomeditor.kernel import Document, GeometryError


class ConstructionTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document()

    def op(self, action, **kw):
        self.doc.operate(dict(action=action, revision=self.doc.revision, **kw))

    def line(self, a=(-60,-30,30), b=(-60,30,30)):
        self.op('node_create', point=list(a))
        self.op('node_create', point=list(b))
        self.op('line_create', nodes=['N1','N2'])

    def test_create_without_geometry_and_fit_bounds(self):
        self.line()
        scene = self.doc.scene()
        self.assertEqual(len(scene['nodes']),2)
        self.assertEqual(scene['lines'][0]['nodes'],['N1','N2'])
        self.assertAlmostEqual(scene['lines'][0]['length'],60)
        self.assertEqual(len(scene['bounds']),2)
        self.assertFalse(scene['bodies'])

    def test_reference_line_imprints_solid_face_and_exports_real_edge(self):
        self.doc.sample('tank'); self.line()
        self.op('split_reference',line='L1',bodies=['B1'],scope='surface',faces=['B1:F1'],sweep=[1,0,0],extend=False)
        s = self.doc.body('B1').shape
        self.assertEqual(len(s.Solids()),1)
        self.assertEqual(len(s.Faces()),7)
        self.assertAlmostEqual(s.Volume(),576000)
        other=Document();other.import_cad(self.doc.export_cad('step'),'result.step')
        self.assertEqual(len(other.bodies[0].shape.Faces()),7)
        self.assertEqual(len(other.bodies[0].shape.Solids()),1)
        self.assertFalse(other.nodes)
        self.assertFalse(other.lines)

    def test_reference_line_splits_solid_and_conserves_volume(self):
        self.doc.sample('tank');self.line()
        self.op('split_reference',line='L1',bodies=['B1'],sweep=[1,0,0],extend=True)
        s=self.doc.body('B1').shape
        self.assertEqual(len(s.Solids()),2)
        self.assertAlmostEqual(sum(x.Volume() for x in s.Solids()),576000)

    def test_move_updates_reference_but_not_previous_cad_cut(self):
        self.doc.sample('tank');self.line()
        self.op('split_reference',line='L1',bodies=['B1'],sweep=[1,0,0])
        before=self.doc.body('B1').shape
        self.op('node_move',node='N1',point=[-60,-30,40])
        self.assertEqual(self.doc.scene()['lines'][0]['points'][0],(-60.,-30.,40.))
        self.assertIs(self.doc.body('B1').shape,before)
        self.op('undo')
        self.assertEqual(self.doc.scene()['lines'][0]['points'][0],(-60.,-30.,30.))

    def test_zero_length_and_dangling_line_rejected_without_history(self):
        self.op('node_create',point=[0,0,0]);self.op('node_create',point=[0,0,0])
        revision=self.doc.revision
        for nodes in (['N1','N1'],['N1','N2'],['N1','N3'],[],None):
            with self.subTest(nodes=nodes):
                with self.assertRaises(GeometryError):self.op('line_create',nodes=nodes)
                self.assertEqual(self.doc.revision,revision)
                self.assertFalse(self.doc.lines)
        with self.assertRaises(GeometryError):self.op('node_create',point=[0,0,float('inf')])

    def test_collapse_connected_line_is_rejected_atomically(self):
        self.line();revision=self.doc.revision
        with self.assertRaises(GeometryError):self.op('node_move',node='N1',point=[-60,30,30])
        self.assertEqual(self.doc.revision,revision)
        self.assertAlmostEqual(self.doc.scene()['lines'][0]['length'],60)

    def test_delete_node_removes_connected_lines_and_undo_restores(self):
        self.line();self.op('construction_delete',construction=['N1'])
        self.assertEqual(len(self.doc.nodes),1);self.assertFalse(self.doc.lines)
        self.op('undo');self.assertEqual(len(self.doc.nodes),2);self.assertEqual(len(self.doc.lines),1)
        self.op('redo');self.assertFalse(self.doc.lines)

    def test_delete_line_keeps_nodes(self):
        self.line();self.op('construction_delete',construction=['L1'])
        self.assertEqual(len(self.doc.nodes),2);self.assertFalse(self.doc.lines)

    def test_project_roundtrip_and_ids_after_restore(self):
        self.line();other=Document();other.load_project(self.doc.save_project())
        self.assertEqual(other.lines,self.doc.lines)
        self.assertEqual(other.scene()['lines'][0]['points'],[n['point'] for n in other.nodes])
        other.operate(dict(action='node_create',revision=other.revision,point=[1,2,3]))
        self.assertEqual(other.nodes[-1]['id'],'N3')
        other.operate(dict(action='line_create',revision=other.revision,nodes=['N2','N3']))
        self.assertEqual(other.lines[-1]['id'],'L2')

    def test_legacy_project_clears_references_and_undo_restores(self):
        legacy=Document();legacy.sample('tank')
        out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(legacy.save_project())) as src, zipfile.ZipFile(out,'w') as dst:
            for name in src.namelist():
                data=src.read(name)
                if name=='manifest.json':
                    m=json.loads(data);m['version']=1;m.pop('nodes');m.pop('lines');data=json.dumps(m).encode()
                dst.writestr(name,data)
        self.line();self.doc.load_project(out.getvalue());self.assertFalse(self.doc.nodes)
        self.op('undo');self.assertEqual(len(self.doc.nodes),2)

    def test_invalid_imported_reference_does_not_replace_document(self):
        self.line();out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.doc.save_project())) as src,zipfile.ZipFile(out,'w') as dst:
            m=json.loads(src.read('manifest.json'));m['lines'][0]['nodes']=['N1','N99'];dst.writestr('manifest.json',json.dumps(m))
        revision=self.doc.revision
        with self.assertRaises(GeometryError):self.doc.load_project(out.getvalue())
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(self.doc.lines[0]['nodes'],['N1','N2'])

    def test_invalid_direction_or_missing_line_preserves_shape(self):
        self.doc.sample('tank');self.line();revision=self.doc.revision
        for line,sweep in [('L99',[1,0,0]),('L1',[0,1,0])]:
            with self.assertRaises(GeometryError):self.op('split_reference',line=line,bodies=['B1'],sweep=sweep)
        self.assertEqual(self.doc.revision,revision)
        self.assertEqual(len(self.doc.body('B1').shape.Faces()),6)

    def test_reference_splits_standalone_surface(self):
        self.doc.sample('surface');self.line((-60,0,10),(60,0,10))
        self.op('split_reference',line='L1',bodies=['B1'],scope='surface',faces=['B1:F1'],sweep=[0,0,1],extend=False)
        s=self.doc.body('B1').shape
        self.assertEqual(len(s.Faces()),2)
        self.assertAlmostEqual(s.Area(),7000)

if __name__ == '__main__':unittest.main()
