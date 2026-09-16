"""Exact B-rep operations. Display tessellation is never used as CAD geometry."""
from __future__ import annotations

import io
import json
import math
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

import cadquery as cq
from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter, BRepAlgoAPI_Section, BRepAlgoAPI_Defeaturing
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.BRepFeat import BRepFeat_SplitShape
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader, IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.ShapeFix import ShapeFix_Shape
from OCP.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCP.TopTools import TopTools_ListOfShape


class GeometryError(ValueError):
    pass


def number(value, name='値', positive=False):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise GeometryError(f'{name}: 数値を入力してください。') from None
    if not math.isfinite(v) or abs(v) > 1e9 or (positive and v <= 0):
        raise GeometryError(f'{name}: 有効な範囲の数値を入力してください。')
    return v


def vector(value, name='座標', nonzero=False):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise GeometryError(f'{name}: x, y, z の3成分が必要です。')
    v = cq.Vector(*(number(x, name) for x in value))
    if nonzero and v.Length < 1e-10:
        raise GeometryError(f'{name}: ゼロベクトルは使えません。')
    return v


def compound(shapes):
    shapes = list(shapes)
    if not shapes:
        raise GeometryError('対象形状がありません。')
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def checked(shape):
    if shape.wrapped.IsNull() or not (shape.Faces() or shape.Edges()):
        raise GeometryError('結果が空です。元の形状を保持しました。')
    if not shape.isValid():
        raise GeometryError('CAD形状の整合性検査に失敗しました。元の形状を保持しました。')
    return shape


def split_with(shape, tools, tolerance):
    args, cutters = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(shape.wrapped)
    for tool in tools:
        cutters.Append(tool.wrapped)
    op = BRepAlgoAPI_Splitter()
    op.SetArguments(args)
    op.SetTools(cutters)
    op.SetNonDestructive(True)
    op.SetFuzzyValue(tolerance)
    op.Build()
    if not op.IsDone():
        raise GeometryError('分割に失敗しました。交差・許容差を確認してください。')
    return checked(cq.Shape.cast(op.Shape()))


def imprint(shape, faces, tool, tolerance):
    """Insert real section edges on selected faces, retaining the original solid."""
    op = BRepFeat_SplitShape(shape.wrapped)
    count = 0
    for face in faces:
        section = BRepAlgoAPI_Section(face.wrapped, tool.wrapped, False)
        section.ComputePCurveOn1(True)
        section.Approximation(True)
        section.SetFuzzyValue(tolerance)
        section.Build()
        if not section.IsDone():
            raise GeometryError('面と工具の交線を計算できません。')
        edges = cq.Shape.cast(section.Shape()).Edges()
        for wire in cq.Wire.combine(edges, tolerance):
            op.Add(wire.wrapped, face.wrapped)
            count += 1
    if not count:
        raise GeometryError('選択面を横切る交線がありません。')
    op.Build()
    if not op.IsDone():
        raise GeometryError('面への分割線挿入に失敗しました。')
    result = checked(cq.Shape.cast(op.Shape()))
    if len(result.Faces()) <= len(shape.Faces()):
        raise GeometryError('面は分割されませんでした。線を面の境界まで延ばしてください。')
    if len(result.Solids()) != len(shape.Solids()):
        raise GeometryError('面分割でソリッド数が変化したため取り消しました。')
    return result


@dataclass(frozen=True)
class Body:
    id: str
    name: str
    shape: cq.Shape


class Document:
    def __init__(self):
        self.bodies: list[Body] = []
        self.nodes = []
        self.lines = []
        self.next_node = 1
        self.next_line = 1
        self.undo_stack = []
        self.redo_stack = []
        self.log = []
        self.revision = 0
        self.next_id = 1
        self.message = 'STEP / IGES を開くか、サンプルから始めてください。'

    def make_body(self, shape, name):
        body = Body(f'B{self.next_id}', str(name)[:120], checked(shape))
        self.next_id += 1
        return body

    def commit(self, bodies, label, nodes=None, lines=None):
        # Validate the entire transaction before publishing any changed shape.
        for body in bodies:
            checked(body.shape)
        nodes = self.nodes if nodes is None else nodes
        lines = self.lines if lines is None else lines
        self.validate_construction(nodes, lines)
        self.undo_stack.append((self.bodies, self.log, self.nodes, self.lines))
        self.undo_stack = self.undo_stack[-30:]
        self.redo_stack.clear()
        self.bodies = bodies
        self.nodes, self.lines = nodes, lines
        self.log = self.log + [label]
        self.revision += 1
        self.message = label

    def history(self, direction):
        source, dest = (self.undo_stack, self.redo_stack) if direction == 'undo' else (self.redo_stack, self.undo_stack)
        if not source:
            raise GeometryError('これ以上戻せません。' if direction == 'undo' else 'やり直す操作がありません。')
        dest.append((self.bodies, self.log, self.nodes, self.lines))
        self.bodies, self.log, self.nodes, self.lines = source.pop()
        self.revision += 1
        self.message = '元に戻しました。' if direction == 'undo' else 'やり直しました。'

    @staticmethod
    def validate_construction(nodes, lines):
        positions = {}
        for node in nodes:
            key = node['id']
            if not isinstance(key, str) or not key.startswith('N') or not key[1:].isdigit() or key in positions:
                raise GeometryError('仮想節点IDが不正または重複しています。')
            positions[key] = vector(node['point'])
        seen = set()
        for line in lines:
            key = line['id']
            if not isinstance(key, str) or not key.startswith('L') or not key[1:].isdigit() or key in seen:
                raise GeometryError('作図ラインIDが不正または重複しています。')
            seen.add(key)
            ends = line['nodes']
            if not isinstance(ends, list) or len(ends) != 2 or any(n not in positions for n in ends):
                raise GeometryError('ラインには存在する2つの仮想節点が必要です。')
            if (positions[ends[1]]-positions[ends[0]]).Length <= 1e-7:
                raise GeometryError('ラインの2節点は 1e-7 mm より離してください。')

    def edge_nodes(self, p):
        _, edge = self.entity(p.get('edge'), 'E')
        curve = BRepAdaptor_Curve(edge.wrapped)
        length = GCPnts_AbscissaPoint.Length_s(curve, 1e-9)
        if not math.isfinite(length) or length <= 1e-7:
            raise GeometryError('長さが 1e-7 mm 以下のエッジには節点を配置できません。')
        reverse = p.get('reverse', False)
        if not isinstance(reverse, bool):
            raise GeometryError('向きの反転指定が不正です。')
        if p['action'] == 'nodes_divide_edge':
            count = number(p.get('divisions'), '分割数')
            if not count.is_integer() or not 1 <= count <= 1000:
                raise GeometryError('分割数 n は 1～1000 の整数にしてください。')
            count = int(count)
            fractions = [i/count for i in range(count+1)]
        else:
            fraction = number(p.get('fraction'), '長さの割合')
            if not 0 <= fraction <= 1:
                raise GeometryError('長さの割合は 0～1 で指定してください。')
            fractions = [fraction]
        # Integrate and invert physical arc length with explicit absolute tolerance.
        # Increasing underlying curve parameter defines the displayed 0 -> 1 direction.
        nodes = []
        for i, fraction in enumerate(fractions):
            t = 1-fraction if reverse else fraction
            if t == 0:
                param = curve.FirstParameter()
            elif t == 1:
                param = curve.LastParameter()
            else:
                solver = GCPnts_AbscissaPoint(1e-9, curve, length*t, curve.FirstParameter())
                if not solver.IsDone():
                    raise GeometryError('エッジ上の弧長位置を計算できません。節点は作成していません。')
                param = solver.Parameter()
                if not curve.FirstParameter() <= param <= curve.LastParameter():
                    raise GeometryError('エッジ範囲外の計算結果を検出したため中止しました。')
            point = vector(cq.Vector(curve.Value(param)).toTuple()).toTuple()
            nodes.append({'id': f'N{self.next_node+i}', 'point': point})
        label = f"{p['edge']} 上に仮想節点を {len(nodes)} 個作成（弧長基準）"
        if edge.IsClosed() and len(nodes) > 1:
            label += '。閉じたエッジの始終点は同じ位置です'
        self.commit(self.bodies, label, self.nodes + nodes)
        self.next_node += len(nodes)

    def construction_operation(self, p):
        action = p['action']
        nodes, lines = list(self.nodes), list(self.lines)
        if action == 'node_create':
            point = vector(p.get('point')).toTuple()
            key = f'N{self.next_node}'
            nodes.append({'id': key, 'point': point})
            label = f'仮想節点 {key} を作成'
        elif action == 'node_move':
            key = p.get('node')
            if not any(n['id'] == key for n in nodes):
                raise GeometryError('移動する仮想節点を選択してください。')
            point = vector(p.get('point')).toTuple()
            nodes = [dict(n, point=point) if n['id'] == key else n for n in nodes]
            label = f'仮想節点 {key} を移動（作図ラインのみ追従）'
        elif action == 'line_create':
            key = f'L{self.next_line}'
            lines.append({'id': key, 'nodes': p.get('nodes')})
            label = f'作図ライン {key} を作成'
        else:
            keys = p.get('construction', [])
            available = {x['id'] for x in nodes + lines}
            if not keys or any(k not in available for k in keys):
                raise GeometryError('削除する仮想節点・作図ラインを選択してください。')
            nodes = [n for n in nodes if n['id'] not in keys]
            lines = [l for l in lines if l['id'] not in keys and not any(n in keys for n in l['nodes'])]
            label = '選択した作図要素と接続ラインを削除'
        self.commit(self.bodies, label, nodes, lines)
        if action == 'node_create':
            self.next_node += 1
        if action == 'line_create':
            self.next_line += 1

    def body(self, key):
        for body in self.bodies:
            if body.id == key:
                return body
        raise GeometryError('対象ボディがありません。選び直してください。')

    def entity(self, key, kind=None):
        try:
            bid, tag = key.split(':')
            obj = self.body(bid)
            t, index = tag[0], int(tag[1:]) - 1
            if kind and t != kind:
                raise ValueError()
            shapes = {'F': obj.shape.Faces, 'E': obj.shape.Edges, 'V': obj.shape.Vertices}[t]()
            if index < 0:
                raise ValueError()
            return obj, shapes[index]
        except (ValueError, IndexError, KeyError, AttributeError):
            raise GeometryError('面・エッジの選択が無効です。選び直してください。') from None

    def import_cad(self, data: bytes, filename: str):
        ext = Path(filename).suffix.lower()
        if ext not in ('.step', '.stp', '.iges', '.igs'):
            raise GeometryError('STEP (.step/.stp) または IGES (.iges/.igs) を選んでください。')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ('input' + ext)
            path.write_bytes(data)
            # Both readers convert declared file units to the mm working unit.
            reader = STEPControl_Reader() if ext in ('.step', '.stp') else IGESControl_Reader()
            Interface_Static.SetCVal_s('xstep.cascade.unit', 'MM')
            if reader.ReadFile(str(path)) != IFSelect_RetDone:
                raise GeometryError('CADファイルを読み込めません。形式または破損を確認してください。')
            if reader.TransferRoots() == 0:
                raise GeometryError('変換可能な形状がありません。')
            shape = checked(cq.Shape.cast(reader.OneShape()))
        # Keep the entire root B-rep: mixed solids/sheets and shared topology survive.
        body = self.make_body(shape, Path(filename).stem)
        self.commit(self.bodies + [body], f'{Path(filename).name} を読み込みました（内部単位 mm）。')

    def export_cad(self, fmt):
        if fmt not in ('step', 'iges', 'brep'):
            raise GeometryError('出力形式が無効です。')
        shape = checked(compound(b.shape for b in self.bodies))
        if fmt == 'brep':
            buf = io.BytesIO()
            shape.exportBrep(buf)
            return buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / ('geometry.' + fmt))
            if fmt == 'step':
                writer = STEPControl_Writer()
                Interface_Static.SetCVal_s('write.step.unit', 'MM')
                Interface_Static.SetCVal_s('write.step.schema', 'AP214IS')
                if writer.Transfer(shape.wrapped, STEPControl_AsIs) != IFSelect_RetDone:
                    raise GeometryError('STEP変換に失敗しました。')
                success = writer.Write(path) == IFSelect_RetDone
            else:
                writer = IGESControl_Writer('MM', 1)
                success = writer.AddShape(shape.wrapped) and writer.Write(path)
            if not success:
                raise GeometryError('CAD出力に失敗しました。')
            return Path(path).read_bytes()

    def save_project(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            manifest = {'format': 'GeomEditor', 'version': 2, 'unit': 'mm', 'log': self.log, 'nodes': self.nodes, 'lines': self.lines, 'bodies': []}
            for i, body in enumerate(self.bodies):
                buf = io.BytesIO()
                body.shape.exportBrep(buf)
                path = f'body-{i}.brep'
                z.writestr(path, buf.getvalue())
                manifest['bodies'].append({'name': body.name, 'file': path})
            z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        return out.getvalue()

    def load_project(self, data):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist()) > 200 * 1024**2:
                    raise GeometryError('展開後のプロジェクトサイズが200 MBを超えています。')
                m = json.loads(z.read('manifest.json'))
                if m['format'] != 'GeomEditor' or m['version'] not in (1, 2) or m['unit'] != 'mm':
                    raise GeometryError('未対応のプロジェクト形式です。')
                bodies = [self.make_body(cq.Shape.importBrep(io.BytesIO(z.read(b['file']))), b['name']) for b in m['bodies']]
                nodes, lines = m.get('nodes', []), m.get('lines', [])
                self.validate_construction(nodes, lines)
                self.commit(bodies, 'プロジェクトを復元しました。', nodes, lines)
                self.next_node = max(self.next_node, max((int(n['id'][1:])+1 for n in nodes), default=1))
                self.next_line = max(self.next_line, max((int(l['id'][1:])+1 for l in lines), default=1))
                self.log = [str(s)[:300] for s in m.get('log', [])][-1000:] + [self.message]
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
            raise GeometryError('プロジェクトを読み込めません。') from e

    def sample(self, name):
        if name == 'tank':
            shape = cq.Solid.makeBox(120, 60, 80, (-60, -30, 0))
            label = '流体領域 120 × 60 × 80 mm'
        elif name == 'bracket':
            base = cq.Workplane('XY').box(100, 60, 10, centered=(True, True, False))
            upright = cq.Workplane('XY').box(16, 60, 70, centered=(True, True, False)).translate((-42, 0, 0))
            shape = base.union(upright).val()
            label = '評価ライン練習用 L形ブラケット'
        elif name == 'surface':
            shape = cq.Face.makePlane(100, 70)
            label = 'サーフェイス 100 × 70 mm'
        elif name == 'cylinder':
            shape = cq.Solid.makeCylinder(30, 80)
            label = '曲面練習用 円柱'
        else:
            raise GeometryError('サンプルがありません。')
        self.commit(self.bodies + [self.make_body(shape, label)], label + ' を追加しました。')

    def operate(self, p):
        if p.get('revision') != self.revision:
            raise GeometryError('モデルが更新されています。画面を更新して選び直してください。')
        action = p.get('action')
        if action in ('undo', 'redo'):
            self.history(action)
            return
        if action == 'sample':
            self.sample(p.get('name'))
            return
        if action in ('node_on_edge', 'nodes_divide_edge'):
            self.edge_nodes(p)
            return
        if action in ('node_create', 'node_move', 'line_create', 'construction_delete'):
            self.construction_operation(p)
            return
        if action == 'split_reference':
            line = next((l for l in self.lines if l['id'] == p.get('line')), None)
            if line is None:
                raise GeometryError('分割工具に使う作図ラインを選択してください。')
            points = {n['id']: n['point'] for n in self.nodes}
            p = dict(p, action='split_line', origin=points[line['nodes'][0]], end=points[line['nodes'][1]])
            action = 'split_line'
        ids = list(dict.fromkeys(p.get('bodies', [])))
        selected = [self.body(key) for key in ids]
        tol = number(p.get('tolerance', 1e-6), '許容差', True)
        if tol > 1:
            raise GeometryError('許容差は 1 mm 以下で指定してください。')
        body = selected[0] if selected else None
        new_bodies = list(self.bodies)
        label = action

        def require_body():
            if not body:
                raise GeometryError('対象ボディを選択してください。')

        def update(shape, name=None):
            nonlocal new_bodies
            require_body()
            new_bodies = [replace(b, shape=checked(shape), name=name or b.name) if b.id == body.id else b for b in new_bodies]

        def add(shape, name):
            new_bodies.append(self.make_body(shape, name))

        if action in ('split_plane', 'split_line', 'split_tool', 'perpendicular', 'parallel'):
            require_body()
            if action == 'split_tool':
                _, tool = self.entity(p.get('tool'), 'F')
            else:
                origin = vector(p.get('origin', [0, 0, 0]))
                if action == 'split_plane':
                    normal = vector(p.get('normal'), '平面法線', True).normalized()
                elif action in ('perpendicular', 'parallel'):
                    _, edge = self.entity(p.get('edge'), 'E')
                    if edge.geomType() != 'LINE':
                        raise GeometryError('この操作の参照エッジは直線に限定しています。')
                    tangent = edge.tangentAt().normalized()
                    normal = tangent if action == 'perpendicular' else tangent.cross(vector(p.get('sweep'), '投影方向', True))
                    if normal.Length < 1e-10:
                        raise GeometryError('参照エッジと投影方向が平行です。')
                    normal = normal.normalized()
                else:
                    end = vector(p.get('end'), '終点')
                    tangent = end - origin
                    sweep = vector(p.get('sweep'), '投影方向', True).normalized()
                    normal = tangent.cross(sweep)
                    if normal.Length < 1e-10:
                        raise GeometryError('2点が同一、または線と投影方向が平行です。')
                    normal = normal.normalized()
                tool = cq.Face.makePlane(basePnt=origin, dir=normal)
                if action == 'split_line' and not p.get('extend', True):
                    extent = body.shape.BoundingBox().DiagonalLength * 2 + (body.shape.Center()-origin).Length
                    wire = cq.Wire.makePolygon([origin-sweep*extent, end-sweep*extent, end+sweep*extent, origin+sweep*extent], close=True)
                    tool = cq.Face.makeFromWires(wire)
            scope = p.get('scope', 'solid')
            if scope == 'surface':
                faces = []
                for key in p.get('faces', []):
                    owner, face = self.entity(key, 'F')
                    if owner.id != body.id:
                        raise GeometryError('対象ボディの面だけを選択してください。')
                    faces.append(face)
                if not faces:
                    raise GeometryError('分割する面を選択してください。')
                result = imprint(body.shape, faces, tool, tol)
            elif scope == 'solid':
                result = split_with(body.shape, [tool], tol)
                if (len(result.Solids()), len(result.Faces()), len(result.Edges())) == (len(body.shape.Solids()), len(body.shape.Faces()), len(body.shape.Edges())):
                    raise GeometryError('新しい分割境界ができません。平面位置・工具との交差を確認してください。')
            else:
                raise GeometryError('分割対象が不正です。')
            update(result)
            label = '面分割（評価ライン）' if scope == 'surface' else '形状分割（共有境界を保持）'
        elif action in ('union', 'difference', 'intersection', 'partition'):
            if len(selected) < 2:
                raise GeometryError('対象→工具の順に2つ以上のボディを選択してください。')
            shapes = [b.shape for b in selected]
            if action == 'partition':
                # All inputs are arguments, so the result retains every region and shared faces.
                args = TopTools_ListOfShape()
                for shape in shapes:
                    args.Append(shape.wrapped)
                op = BRepAlgoAPI_Splitter()
                op.SetArguments(args)
                op.SetNonDestructive(True)
                op.SetFuzzyValue(tol)
                op.Build()
                if not op.IsDone():
                    raise GeometryError('共有境界を持つ結合に失敗しました。')
                result = cq.Shape.cast(op.Shape())
            elif action == 'intersection':
                result = shapes[0]
                for tool in shapes[1:]:
                    result = checked(result.intersect(tool, tol=tol))
            else:
                method = {'union': 'fuse', 'difference': 'cut'}[action]
                result = getattr(shapes[0], method)(*shapes[1:], tol=tol)
            update(result)
            if not p.get('keep_tools', False):
                new_bodies = [b for b in new_bodies if b.id not in ids[1:]]
            label = {'union':'和集合', 'difference':'差集合', 'intersection':'共通部分', 'partition':'共有境界付き結合'}[action]
        elif action in ('translate', 'rotate', 'scale', 'mirror'):
            require_body()
            for b in selected:
                if action == 'translate':
                    shape = b.shape.translate(vector(p.get('delta'), '移動量'))
                elif action == 'rotate':
                    origin = vector(p.get('origin', [0,0,0]))
                    axis = vector(p.get('axis'), '回転軸', True)
                    shape = b.shape.rotate(origin, origin+axis, number(p.get('angle'), '角度'))
                elif action == 'scale':
                    shape = b.shape.scale(number(p.get('factor'), '倍率', True))
                else:
                    shape = b.shape.mirror(vector(p.get('normal'), '鏡映面法線', True), vector(p.get('origin', [0,0,0])))
                if p.get('copy'):
                    add(shape, b.name + ' copy')
                else:
                    new_bodies = [replace(x, shape=checked(shape)) if x.id == b.id else x for x in new_bodies]
            label = {'translate':'平行移動', 'rotate':'回転', 'scale':'原点基準の拡大縮小', 'mirror':'鏡映'}[action]
        elif action == 'delete':
            require_body()
            new_bodies = [b for b in new_bodies if b.id not in ids]
            label = '選択ボディを削除'
        elif action == 'rename':
            require_body()
            name = str(p.get('name', '')).strip()[:120]
            if not name:
                raise GeometryError('名前を入力してください。')
            update(body.shape, name)
            label = 'ボディ名を変更'
        elif action == 'explode':
            require_body()
            # Reject mixed-dimensional compounds to avoid silently dropping sheet bodies.
            solids = body.shape.Solids()
            if (len(solids) < 2
                    or set(f for s in solids for f in s.Faces()) != set(body.shape.Faces())
                    or set(e for s in solids for e in s.Edges()) != set(body.shape.Edges())):
                raise GeometryError('複数ソリッドのみで構成されたボディを選択してください。')
            new_bodies = [b for b in new_bodies if b.id != body.id]
            for i, solid in enumerate(solids):
                add(solid, f'{body.name} / {i+1}')
            label = 'ソリッドごとにボディを分離'
        elif action in ('heal', 'unify'):
            require_body()
            if action == 'heal':
                fixer = ShapeFix_Shape(body.shape.wrapped)
                fixer.SetPrecision(tol)
                fixer.SetMaxTolerance(tol)
                fixer.Perform()
                result = cq.Shape.cast(fixer.Shape())
            else:
                result = body.shape.clean()
            update(result)
            label = '形状修復' if action == 'heal' else '同一面の分割解除（評価ラインも消える場合があります）'
        elif action == 'sew':
            require_body()
            if any(b.shape.Solids() for b in selected):
                raise GeometryError('縫合は独立サーフェイス・シェルを選んでください。ソリッドは対象外です。')
            op = BRepBuilderAPI_Sewing(tol)
            for b in selected:
                op.Add(b.shape.wrapped)
            op.Perform()
            result = checked(cq.Shape.cast(op.SewedShape()))
            if p.get('make_solid'):
                shells = result.Shells()
                if (not shells or any(not s.Closed() for s in shells)
                        or set(f for s in shells for f in s.Faces()) != set(result.Faces())):
                    raise GeometryError('閉じたシェルになっていません。隙間と自由エッジを確認してください。')
                result = compound(cq.Solid.makeSolid(s) for s in shells)
            update(result)
            new_bodies = [b for b in new_bodies if b.id not in ids[1:]]
            label = 'サーフェイス縫合' + ('・ソリッド化' if p.get('make_solid') else '')
        elif action in ('extract', 'offset', 'extrude', 'defeature'):
            faces = []
            for key in p.get('faces', []):
                owner, face = self.entity(key, 'F')
                if action == 'defeature' and (not body or owner.id != body.id):
                    raise GeometryError('除去する面は対象ボディから選んでください。')
                faces.append(face)
            if not faces:
                raise GeometryError('面を選択してください。')
            if action == 'extract':
                add(compound(faces), '抽出サーフェイス')
            elif action == 'offset':
                distance = number(p.get('distance'), 'オフセット量')
                if abs(distance) <= tol:
                    raise GeometryError('オフセット量を許容差より大きくしてください。')
                for face in faces:
                    op = BRepOffsetAPI_MakeOffsetShape()
                    op.PerformByJoin(face.wrapped, distance, tol)
                    if not op.IsDone():
                        raise GeometryError('オフセットに失敗しました。曲率と距離を確認してください。')
                    add(cq.Shape.cast(op.Shape()), 'オフセットサーフェイス')
            elif action == 'extrude':
                direction = vector(p.get('delta'), '押出し量', True)
                for face in faces:
                    if face.geomType() != 'PLANE':
                        raise GeometryError('押出しは平面フェイスに限定しています。')
                    add(cq.Solid.extrudeLinear(face.outerWire(), face.innerWires(), direction), '押出しソリッド')
            else:
                require_body()
                if not body.shape.Solids():
                    raise GeometryError('面除去・修復はソリッドに限定しています。')
                op = BRepAlgoAPI_Defeaturing()
                op.SetShape(body.shape.wrapped)
                for face in faces:
                    op.AddFaceToRemove(face.wrapped)
                op.Build()
                if not op.IsDone():
                    raise GeometryError('面除去後に閉じた形状を再構成できません。')
                result = checked(cq.Shape.cast(op.Shape()))
                if len(result.Faces()) >= len(body.shape.Faces()):
                    raise GeometryError('面を除去できませんでした。')
                update(result)
            label = {'extract':'面を独立サーフェイスとして複製', 'offset':'オフセット面を作成', 'extrude':'面を押出し', 'defeature':'選択面を除去・周囲を修復'}[action]
        elif action in ('fillet', 'chamfer'):
            require_body()
            if (len(body.shape.Solids()) != 1
                    or set(body.shape.Solids()[0].Faces()) != set(body.shape.Faces())
                    or set(body.shape.Solids()[0].Edges()) != set(body.shape.Edges())):
                raise GeometryError('独立面や線を含まない単一ソリッドを選択してください。')
            solid = body.shape.Solids()[0]
            edges = []
            for key in p.get('edges', []):
                owner, edge = self.entity(key, 'E')
                if owner.id != body.id:
                    raise GeometryError('対象ボディのエッジを選んでください。')
                edges.append(edge)
            if not edges:
                raise GeometryError('エッジを選んでください。')
            size = number(p.get('size'), '寸法', True)
            update(solid.fillet(size, edges) if action == 'fillet' else solid.chamfer(size, None, edges))
            label = 'フィレット' if action == 'fillet' else '面取り'
        elif action == 'patch':
            edges = [self.entity(key, 'E')[1] for key in p.get('edges', [])]
            wires = cq.Wire.combine(edges, tol)
            if len(wires) != 1 or not wires[0].IsClosed():
                raise GeometryError('1つの閉じた平面ループを作るエッジを選んでください。')
            add(cq.Face.makeFromWires(wires[0]), '平面パッチ')
            label = '平面の穴埋め面を作成（縫合は別操作）'
        else:
            raise GeometryError('未対応の操作です。')
        self.commit(new_bodies, label + ' を実行しました。')

    def measure(self, keys):
        if len(keys) != 2:
            raise GeometryError('測定対象の面・エッジを2つ選んでください。')
        a, b = (self.entity(k)[1] if ':' in k else self.body(k).shape for k in keys)
        return {'distance': a.distance(b), 'unit': 'mm'}

    def scene(self):
        bodies, faces_out, edges_out = [], [], []
        bounds = []
        for body in self.bodies:
            shape = body.shape
            bbox = shape.BoundingBox()
            bounds += [[bbox.xmin,bbox.ymin,bbox.zmin], [bbox.xmax,bbox.ymax,bbox.zmax]]
            faces, edges = shape.Faces(), shape.Edges()
            adjacency = {e: [] for e in edges}
            for i, face in enumerate(faces):
                fid = f'{body.id}:F{i+1}'
                for e in face.Edges():
                    adjacency[e].append(fid)
                vertices, triangles = face.tessellate(max(bbox.DiagonalLength/1200, 0.005), 0.15)
                faces_out.append({'id':fid, 'body':body.id, 'type':face.geomType(), 'area':face.Area(), 'center':face.Center().toTuple(), 'vertices':[v.toTuple() for v in vertices], 'triangles':triangles})
            counts = {'free':0, 'shared':0, 'nonmanifold':0, 'seam':0, 'wire':0}
            for i, edge in enumerate(edges):
                owners = adjacency[edge]
                # Periodic seam edges belong to one face but occur twice in its wire.
                from OCP.BRep import BRep_Tool
                seam = any(BRep_Tool.IsClosed_s(edge.wrapped, faces[int(f.split(':F')[1])-1].wrapped) for f in owners)
                state = 'wire' if not owners else 'seam' if seam and len(owners)==1 else 'free' if len(owners)==1 else 'shared' if len(owners)==2 else 'nonmanifold'
                counts[state] += 1
                pts, _ = edge.sample(2 if edge.geomType() == 'LINE' else 64)
                if edge.IsClosed() and pts and (pts[0]-pts[-1]).Length > 1e-10:
                    pts.append(pts[0])
                curve = BRepAdaptor_Curve(edge.wrapped)
                edges_out.append({'id':f'{body.id}:E{i+1}', 'body':body.id, 'type':edge.geomType(), 'length':GCPnts_AbscissaPoint.Length_s(curve, 1e-9), 'closed':edge.IsClosed(), 'start':cq.Vector(curve.Value(curve.FirstParameter())).toTuple(), 'end':cq.Vector(curve.Value(curve.LastParameter())).toTuple(), 'state':state, 'faces':owners, 'points':[v.toTuple() for v in pts]})
            bodies.append({'id':body.id, 'name':body.name, 'solids':len(shape.Solids()), 'faces':len(faces), 'edges':len(edges), 'volume':sum(s.Volume() for s in shape.Solids()), 'area':shape.Area(), 'valid':shape.isValid(), 'topology':counts, 'bounds':[[bbox.xmin,bbox.ymin,bbox.zmin],[bbox.xmax,bbox.ymax,bbox.zmax]]})
        bounds.extend(n['point'] for n in self.nodes)
        positions = {n['id']: n['point'] for n in self.nodes}
        lines = [dict(l, points=[positions[k] for k in l['nodes']], length=(vector(positions[l['nodes'][1]])-vector(positions[l['nodes'][0]])).Length) for l in self.lines]
        return {'nodes':self.nodes, 'lines':lines, 'revision':self.revision, 'unit':'mm', 'bodies':bodies, 'faces':faces_out, 'edges':edges_out, 'bounds':bounds, 'undo':bool(self.undo_stack), 'redo':bool(self.redo_stack), 'log':self.log[-50:], 'message':self.message}
