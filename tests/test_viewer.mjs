// Pure projection/picking tests. These do not replace real WebGL/browser testing.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source=readFileSync(new URL('../web/viewer.js',import.meta.url),'utf8');
const {Viewer}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function viewer(){const v=Object.create(Viewer.prototype);Object.assign(v,{canvas:{clientWidth:600,clientHeight:400},center:[0,0,0],radius:100,zoom:1,yaw:0,pitch:0,mode:'face',hidden:new Set(),data:{faces:[],edges:[],bounds:[[-50,-50,-50],[50,50,50]]},draw(){}});return v}
const face=(id,x)=>({id,body:id.split(':')[0],vertices:[[x,-30,-30],[x,30,-30],[x,0,30]],triangles:[[0,1,2]]});
test('orthographic projection respects world axes',()=>{const v=viewer();assert.deepEqual(v.project([0,20,10]),[340,180,0])});
test('front face wins independently of array order',()=>{const v=viewer();v.data.faces=[face('B1:F1',30),face('B1:F2',-30)];let hit;v.onPick=h=>hit=h;v.pick(300,200,false);assert.equal(hit.id,'B1:F1');assert.ok(Math.abs(hit.point[0]-30)<1e-8)});
test('hidden bodies cannot be selected',()=>{const v=viewer();v.hidden.add('B1');v.data.faces=[face('B1:F1',30)];let hit;v.onPick=h=>hit=h;v.pick(300,200,false);assert.equal(hit,null)});
test('body mode selects owner and passes multiselect',()=>{const v=viewer();v.mode='body';v.data.faces=[face('B1:F1',30)];let hit,m;v.onPick=(h,multi)=>{hit=h;m=multi};v.pick(300,200,true);assert.equal(hit.id,'B1');assert.equal(m,true)});
test('back edges are occluded in shaded mode',()=>{const v=viewer();v.mode='edge';v.data.faces=[face('B1:F1',30)];v.data.edges=[{id:'B1:E1',body:'B1',type:'LINE',points:[[-30,-20,0],[-30,20,0]]}];let hit;v.onPick=h=>hit=h;v.pick(300,200,false);assert.equal(hit,null);v.wireOnly=true;v.pick(300,200,false);assert.equal(hit.id,'B1:E1')});
test('curved edge sample points are labelled approximate',()=>{const v=viewer();v.mode='edge';v.data.edges=[{id:'B1:E1',body:'B1',type:'CIRCLE',points:[[0,-20,0],[0,0,5],[0,20,0]]}];let hit;v.onPick=h=>hit=h;v.pick(320,195,false);assert.equal(hit.id,'B1:E1');assert.equal(hit.exact,false)});
test('fit includes narrow viewports',()=>{const v=viewer();v.canvas.clientWidth=200;v.fit();for(const p of v.data.bounds){const q=v.project(p);assert.ok(q[0]>=0&&q[0]<=200);assert.ok(q[1]>=0&&q[1]<=400)}});
