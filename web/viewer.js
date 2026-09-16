// Dependency-free orthographic CAD viewport. Only exact B-rep edges are drawn.
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=a=>{const l=Math.hypot(...a)||1;return a.map(v=>v/l)};
const colors=['#91aaba','#86afb0','#a6a2bd','#aaa88b','#93aaa1'];
const rgb=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255);
export class Viewer {
  constructor(canvas,onPick){
    this.canvas=canvas;this.onPick=onPick;this.gl=canvas.getContext('webgl',{antialias:true,alpha:false,preserveDrawingBuffer:true});
    if(!this.gl)throw Error('WebGL を利用できません。ブラウザーのハードウェアアクセラレーションを確認してください。');
    const gl=this.gl;
    const vs=`attribute vec3 position;attribute vec3 normal;uniform vec3 right;uniform vec3 up;uniform vec3 direction;uniform vec3 center;uniform vec2 scale;uniform float depthScale;uniform float bias;varying vec3 n;void main(){vec3 p=position-center;gl_Position=vec4(dot(p,right)*scale.x,dot(p,up)*scale.y,-dot(p,direction)/depthScale+bias,1.);n=normal;}`;
    const fs=`precision mediump float;uniform vec4 color;uniform float lit;varying vec3 n;void main(){float light=0.72+0.28*abs(dot(normalize(n+vec3(0.0001)),normalize(vec3(0.4,-0.5,0.8))));gl_FragColor=vec4(color.rgb*mix(1.,light,lit),color.a);}`;
    const shader=(type,src)=>{const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s};
    this.program=gl.createProgram();gl.attachShader(this.program,shader(gl.VERTEX_SHADER,vs));gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(this.program);
    if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
    gl.useProgram(this.program);this.locations={};
    for(const key of ['right','up','direction','center','scale','depthScale','color','lit','bias'])this.locations[key]=gl.getUniformLocation(this.program,key);
    this.pos=gl.getAttribLocation(this.program,'position');this.norm=gl.getAttribLocation(this.program,'normal');
    this.center=[0,0,0];this.radius=100;this.zoom=1;this.yaw=-Math.PI/3;this.pitch=.55;this.items=[];this.selected=new Set();this.hidden=new Set();this.topology=true;this.wireOnly=false;this.mode='body';this.plane=null;
    this.resizeObserver=new ResizeObserver(()=>this.draw());this.resizeObserver.observe(canvas);
    canvas.addEventListener('contextmenu',e=>e.preventDefault());
    canvas.addEventListener('pointerdown',e=>{canvas.setPointerCapture(e.pointerId);this.drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,button:e.button,shift:e.shiftKey,moved:false}});
    canvas.addEventListener('pointermove',e=>{if(!this.drag)return;const d=this.drag,dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.hypot(e.clientX-d.startX,e.clientY-d.startY)>4)d.moved=true;
      if(d.button===2||d.button===1||d.shift){const b=this.basis(),s=2*this.radius/this.zoom/canvas.clientHeight;this.center=this.center.map((v,i)=>v-dx*s*b.r[i]+dy*s*b.u[i])}else{this.yaw-=dx*.007;this.pitch=Math.max(-Math.PI/2+.001,Math.min(Math.PI/2-.001,this.pitch+dy*.007))}d.x=e.clientX;d.y=e.clientY;this.draw()});
    canvas.addEventListener('pointerup',e=>{if(this.drag&&!this.drag.moved&&this.drag.button===0){const rect=canvas.getBoundingClientRect();this.pick(e.clientX-rect.left,e.clientY-rect.top,e.ctrlKey||e.metaKey)}this.drag=null});
    canvas.addEventListener('pointercancel',()=>this.drag=null);
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoom=Math.min(1000,Math.max(.01,this.zoom*Math.exp(-e.deltaY*.001)));this.draw()},{passive:false});
  }
  basis(){const c=Math.cos(this.yaw),s=Math.sin(this.yaw),p=Math.sin(this.pitch),q=Math.cos(this.pitch);return{r:[-s,c,0],u:[-p*c,-p*s,q],d:[q*c,q*s,p]}}
  buffer(points,normals,mode,meta){const gl=this.gl;const data=new Float32Array(points.flatMap((p,i)=>[...p,...(normals?.[i]||[0,0,1])]));const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);return{...meta,buffer,count:points.length,mode}}
  setScene(data,fit=false){const gl=this.gl;for(const item of this.items)gl.deleteBuffer(item.buffer);this.items=[];this.data=data;
    data.faces.forEach(f=>{const pts=[],ns=[];for(const tri of f.triangles){const [a,b,c]=tri.map(i=>f.vertices[i]),n=unit(cross(sub(b,a),sub(c,a)));pts.push(a,b,c);ns.push(n,n,n)}this.items.push(this.buffer(pts,ns,gl.TRIANGLES,{id:f.id,body:f.body,face:true}))});
    data.edges.forEach(e=>{const pts=[];for(let i=1;i<e.points.length;i++)pts.push(e.points[i-1],e.points[i]);this.items.push(this.buffer(pts,null,gl.LINES,{id:e.id,body:e.body,face:false,state:e.state}))});
    if(fit)this.fit();else this.draw();
  }
  fit(){const b=this.data?.bounds;if(b?.length){const min=[0,1,2].map(i=>Math.min(...b.map(p=>p[i]))),max=[0,1,2].map(i=>Math.max(...b.map(p=>p[i])));this.center=min.map((v,i)=>(v+max[i])/2);this.radius=Math.max(Math.hypot(...sub(max,min))*.62,1)}this.zoom=Math.min(1,this.canvas.clientWidth/this.canvas.clientHeight);this.draw()}
  view(name){const views={iso:[-Math.PI/3,.55],x:[0,0],y:[Math.PI/2,0],z:[-Math.PI/2,Math.PI/2],'-x':[Math.PI,0],'-y':[-Math.PI/2,0],'-z':[-Math.PI/2,-Math.PI/2]};[this.yaw,this.pitch]=views[name];this.draw()}
  setPlane(origin,normal,enabled){const gl=this.gl;if(this.plane){gl.deleteBuffer(this.plane.buffer);this.plane=null}if(enabled&&Math.hypot(...normal)>1e-9){const n=unit(normal),u=unit(cross(n,Math.abs(n[2])<.9?[0,0,1]:[0,1,0])),v=cross(n,u),size=this.radius;const corner=(a,b)=>origin.map((x,i)=>x+size*(a*u[i]+b*v[i]));const a=corner(-1,-1),b=corner(1,-1),c=corner(1,1),d=corner(-1,1);this.plane=this.buffer([a,b,c,a,c,d],null,gl.TRIANGLES,{})}this.draw()}
  draw(){const gl=this.gl,canvas=this.canvas,dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,canvas.clientWidth),h=Math.max(1,canvas.clientHeight);if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.955,.965,.974,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.useProgram(this.program);
    const b=this.basis(),l=this.locations;gl.uniform3fv(l.right,b.r);gl.uniform3fv(l.up,b.u);gl.uniform3fv(l.direction,b.d);gl.uniform3fv(l.center,this.center);gl.uniform2f(l.scale,this.zoom/this.radius*h/w,this.zoom/this.radius);gl.uniform1f(l.depthScale,this.radius*20);gl.uniform1f(l.bias,0);
    const render=(item,color,lit,bias=0)=>{gl.bindBuffer(gl.ARRAY_BUFFER,item.buffer);gl.enableVertexAttribArray(this.pos);gl.enableVertexAttribArray(this.norm);gl.vertexAttribPointer(this.pos,3,gl.FLOAT,false,24,0);gl.vertexAttribPointer(this.norm,3,gl.FLOAT,false,24,12);gl.uniform4fv(l.color,color);gl.uniform1f(l.lit,lit);gl.uniform1f(l.bias,bias);gl.drawArrays(item.mode,0,item.count)};
    for(const item of this.items){if(this.hidden.has(item.body)||(!item.face)||this.wireOnly)continue;const idx=this.data.bodies.findIndex(b=>b.id===item.body),sel=this.selected.has(item.id)||this.selected.has(item.body);render(item,[...rgb(sel?'#dfb879':colors[idx%colors.length]),1],1)}
    for(const item of this.items){if(this.hidden.has(item.body)||item.face)continue;const sel=this.selected.has(item.id),palette={free:'#d25b55',shared:'#277f77',nonmanifold:'#b28a19',seam:'#7896a8',wire:'#7c67b0'};render(item,[...rgb(sel?'#f14934':this.topology?palette[item.state]:'#394e5c'),1],0,-.0001)}
    if(this.plane){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);render(this.plane,[.05,.62,.72,.18],0);gl.depthMask(true);gl.disable(gl.BLEND)}
    if(this.onView)this.onView({basis:b,span:2*this.radius/this.zoom});
  }
  project(point){const p=sub(point,this.center),b=this.basis(),s=this.canvas.clientHeight*this.zoom/this.radius/2;return[this.canvas.clientWidth/2+dot(p,b.r)*s,this.canvas.clientHeight/2-dot(p,b.u)*s,dot(p,b.d)]}
  pick(x,y,multi){if(!this.data)return;let faceHit=null,edgeHit=null;
    for(const f of this.data.faces){if(this.hidden.has(f.body))continue;const points=f.vertices.map(p=>this.project(p));for(const tri of f.triangles){const [a,b,c]=tri.map(i=>points[i]),den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);if(Math.abs(den)<1e-12)continue;const u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den,v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den,w=1-u-v;if(u>=0&&v>=0&&w>=0){const depth=u*a[2]+v*b[2]+w*c[2];if(!faceHit||depth>faceHit.depth){const point=[0,1,2].map(j=>u*f.vertices[tri[0]][j]+v*f.vertices[tri[1]][j]+w*f.vertices[tri[2]][j]);faceHit={id:this.mode==='body'?f.body:f.id,depth,point,exact:false}}}}}
    for(const edge of this.data.edges){if(this.hidden.has(edge.body))continue;for(let i=1;i<edge.points.length;i++){const p=edge.points[i-1],q=edge.points[i],a=this.project(p),b=this.project(q),dx=b[0]-a[0],dy=b[1]-a[1],len=dx*dx+dy*dy;const t=len?Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/len)):0;const dist=Math.hypot(x-a[0]-t*dx,y-a[1]-t*dy),depth=a[2]+t*(b[2]-a[2]);if(dist<7&&(this.wireOnly||!faceHit||depth>=faceHit.depth-this.radius*.002)&&(!edgeHit||dist<edgeHit.dist)){let point=p.map((v,j)=>v+t*(q[j]-v)),exact=edge.type==='LINE';if(i===1&&Math.hypot(x-a[0],y-a[1])<8){point=p;exact=true}else if(i===edge.points.length-1&&Math.hypot(x-b[0],y-b[1])<8){point=q;exact=true}edgeHit={id:edge.id,dist,depth,point,exact}}}}
    const hit=this.mode==='edge'?edgeHit:faceHit;if(hit&&edgeHit&&this.mode!=='edge'){hit.point=edgeHit.point;hit.exact=edgeHit.exact}this.onPick(hit,multi);
  }
}
