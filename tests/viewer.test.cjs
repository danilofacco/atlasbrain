const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../atlasbrain/static/index.html'),'utf8');
const labelCode=source.slice(source.indexOf('function drawNearbyLabels('),source.indexOf('function draw3d('));
function labels(nodes,options={}){
  const drawn=[];
  const ctx={setTransform(){},measureText(s){return {width:s.length*6}},beginPath(){},roundRect(){},fill(){},fillText(text,x,y){drawn.push({text,x,y,opacity:this.globalAlpha})}};
  const sandbox={N:nodes,ctx,W:600,H:600,dpr:1,cam:{x:0,y:0,k:1},hover:-1,selected:-1,pointer:{x:300,y:300},adj:nodes.map(()=>new Set()),getComputedStyle:()=>({fontFamily:'sans-serif'}),document:{body:{}},...options};
  vm.createContext(sandbox);vm.runInContext(labelCode+'\ndrawNearbyLabels(false);',sandbox);return drawn;
}
const node=(label,x,y)=>({label,x,y,r:5,kind:'codigo',tipo:null});
const brainCode=source.slice(source.indexOf('function brainDimensions('),source.indexOf('function arrangeRadial('));
function brainLayout(count){
  const nodes=Array.from({length:count},(_,i)=>({id:'file-'+i,c:i%5,deg:count-i,isolated:i%31===0,x:i,y:-i,vx:1,vy:1}));
  const sandbox={N:nodes,freePositions:new Map(),alpha:1,needs:false};
  vm.createContext(sandbox);vm.runInContext(brainCode+'\narrangeBrain();',sandbox);
  return sandbox;
}
test('2D brain layout has a lateral cortex, lower rear cerebellum and stem',()=>{
  const {N,alpha}=brainLayout(165);
  assert.equal(N.length,165);assert.equal(alpha,0);
  assert(N.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)&&n.vx===0&&n.vy===0));
  assert(N.filter(n=>n.brainPart==='cortex'&&n.x<0).length>35);
  assert(N.filter(n=>n.brainPart==='cortex'&&n.x>0).length>35);
  assert(N.filter(n=>n.brainPart==='cerebellum').every(n=>n.x>90&&n.y>40));
  assert(N.filter(n=>n.brainPart==='stem').every(n=>n.y>130));
  assert(Math.max(...N.map(n=>n.y))>250);
  assert(Math.max(...N.map(n=>n.x))-Math.min(...N.map(n=>n.x))>500);
});
test('brain layout remains deterministic and finite for small graphs',()=>{
  for(const size of [1,5,24,800]){
    const a=brainLayout(size).N,b=brainLayout(size).N;
    assert(a.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)));
    assert.deepEqual(a.map(n=>[n.x,n.y]),b.map(n=>[n.x,n.y]));
  }
});
test('3D brain gives bounded, deterministic depth to cortex, cerebellum and stem',()=>{
  const code=source.slice(source.indexOf('const orbit='),source.indexOf('function set3d('));
  for(const count of [5,165,300]){
    const sandbox=brainLayout(count);
    sandbox.performance={now:()=>0};
    vm.runInContext(code+`\npoints=N.map(n=>({part:n.brainPart,...brainPoint3d(n),projection:project3d(n,volumeRadius())}));dims=brainDimensions(N.length);`,sandbox);
    const first=sandbox.points.map(p=>[p.x,p.y,p.z,p.projection.x,p.projection.y]);
    assert(first.flat().every(Number.isFinite));
    const again=brainLayout(count);again.performance={now:()=>0};vm.runInContext(code+'\ndepth=N.map(n=>brainPoint3d(n).z)',again);
    assert.deepEqual(sandbox.points.map(p=>p.z),again.depth);
    for(const p of sandbox.points){
      if(p.part==='cortex'){
        const u=(p.x/sandbox.dims.sx+.08)/.94,v=(p.y/sandbox.dims.sy+.16)/.72;
        const max=(.06+.62*Math.sqrt(Math.max(0,1-Math.min(1,u*u+v*v))))*sandbox.dims.sx;
        assert(Math.abs(p.z)<=max+1e-6);
      }
      if(p.part==='stem')assert(Math.abs(p.z)<=.075*sandbox.dims.sx+1e-6);
    }
    vm.runInContext('orbit.yaw+=.8;rotated=N.map(n=>project3d(n,volumeRadius()))',sandbox);
    assert(sandbox.rotated.some((p,i)=>Math.abs(p.x-first[i][3])>1));
    assert(sandbox.rotated.every(p=>[p.x,p.y,p.depth,p.scale].every(Number.isFinite)));
  }
});
test('cortex pulse spreads outward in 2D and 3D, stops on selection or hover, and respects reduced motion',()=>{
  const code=source.slice(source.indexOf('const orbit='),source.indexOf('function set3d('));
  const sandbox=brainLayout(165);
  Object.assign(sandbox,{performance:{now:()=>0},RM:false,selected:-1,hover:-1,radialMode:false,modo:'arquivos'});
  vm.runInContext(code+`\npulse2=cortexPulseState(false,2200);pulse3=cortexPulseState(true,2200);peak=cortexPulseIntensity(.5,.5);far=cortexPulseIntensity(.8,.5);`,sandbox);
  assert.equal(sandbox.pulse2.phase,.5);assert.equal(sandbox.pulse3.phase,.5);
  assert(sandbox.pulse2.dist.every(d=>d>=0&&d<=1));
  assert(sandbox.pulse3.dist.every(d=>d>=0&&d<=1));
  assert.equal(sandbox.peak,1);assert.equal(sandbox.far,0);
  vm.runInContext('selected=0;stopped2=cortexPulseState(false);stopped3=cortexPulseState(true);selected=-1;hover=0;hovered2=cortexPulseState(false);hovered3=cortexPulseState(true);hover=-1;RM=true;reduced=cortexPulseState(false)',sandbox);
  assert.equal(sandbox.stopped2,null);assert.equal(sandbox.stopped3,null);
  assert.equal(sandbox.hovered2,null);assert.equal(sandbox.hovered3,null);assert.equal(sandbox.reduced,null);
});
test('cortex pulse limits illuminated connections per frame',()=>{
  const code=source.slice(source.indexOf('const orbit='),source.indexOf('function set3d('));
  let strokes=0;
  const ctx={save(){},restore(){},setLineDash(){},createLinearGradient(){return {addColorStop(){}}},beginPath(){},moveTo(){},lineTo(){},stroke(){strokes++}};
  const sandbox={performance:{now:()=>0},ctx,cam:{k:1},E:Array.from({length:500},()=>({a:0,b:1}))};
  vm.createContext(sandbox);
  vm.runInContext(code+`\ndrawCortexPulseEdges([{x:0,y:0},{x:100,y:0}],{dist:[0,1],phase:.5},false,0)`,sandbox);
  assert.equal(strokes,180);
});
test('cortex pulse lights nodes without changing their radius or semantic colors',()=>{
  const code=source.slice(source.indexOf('function drawCortexPulseNode('),source.indexOf('function drawBrainGuide('));
  const draws=[];
  const ctx={save(){},restore(){},beginPath(){},arc(_x,_y,r){this.radius=r},fill(){draws.push({shape:'fill',color:this.fillStyle,r:this.radius,alpha:this.globalAlpha})},stroke(){draws.push({shape:'stroke',color:this.strokeStyle,r:this.radius,alpha:this.globalAlpha})}};
  const sandbox={ctx,nodeColor:()=> '#3FEEC5',star(){ctx.radius=7}};
  vm.createContext(sandbox);vm.runInContext(code,sandbox);
  vm.runInContext("drawCortexPulseNode({kind:'codigo'},0,0,5,0)",sandbox);
  assert.equal(draws.length,0);
  vm.runInContext("drawCortexPulseNode({kind:'codigo'},0,0,5,1)",sandbox);
  assert.deepEqual(draws.pop(),{shape:'fill',color:'#D7FFF2',r:5,alpha:.78});
  vm.runInContext("drawCortexPulseNode({kind:'codigo',isolated:true},0,0,5,1)",sandbox);
  assert.equal(draws.pop().color,'#FFD7DD');
  vm.runInContext("drawCortexPulseNode({kind:'tag'},0,0,5,1)",sandbox);
  assert.deepEqual(draws.pop(),{shape:'fill',color:'#FFF2AE',r:3.75,alpha:.78});
  vm.runInContext("drawCortexPulseNode({kind:'fantasma'},0,0,5,1)",sandbox);
  assert.equal(draws.pop().shape,'stroke');
});
test('node size adapts to graph density and the interface has only three modes',()=>{
  const sizes=[10,165,1000].map(count=>{
    const sandbox=brainLayout(count);
    vm.runInContext('scale=nodeScale();maxPx=nodeMaxPx()',sandbox);
    return [sandbox.scale,sandbox.maxPx];
  });
  assert(sizes[0][0]>sizes[1][0]&&sizes[1][0]>sizes[2][0]);
  assert(sizes[0][1]>sizes[1][1]&&sizes[1][1]>sizes[2][1]);
  assert(!source.includes('id="modeBrain"'));
  assert(source.slice(source.indexOf('async function loadGraph('),source.indexOf('function tick(')).includes('arrangeBrain();'));
});
test('large graph UI pages files and search locates a result',()=>{
  assert(source.includes('graphStart=d.inicio||0'));
  assert(source.includes('graphEnd=d.fim||0'));
  assert(source.includes('foco=${encodeURIComponent(focusPath||'));
  assert(source.includes('await loadGraph(false,r.path)'));
  assert(!source.includes('showFolderPreview('));
  assert(!source.includes('pasta/preview'));
});
test('proximity labels fade and disappear outside the radius',()=>{
  const out=labels([node('near',0,0),node('far',0,140),node('outside',0,-200)]);
  assert.deepEqual(out.map(x=>x.text),['near','far']);
  assert.equal(out[0].opacity,1);assert(out[1].opacity>0&&out[1].opacity<.2);
});
test('hover labels are limited to the node and connected neighbors',()=>{
  const out=labels([node('focus',0,0),node('neighbor',0,70),node('unrelated',0,-70)],{hover:0,adj:[new Set([1]),new Set([0]),new Set()]});
  assert.deepEqual(out.map(x=>x.text),['focus','neighbor']);
});
test('labels are bounded and overlapping labels are omitted',()=>{
  const crowded=Array.from({length:30},(_,i)=>node('n'+i,0,0));
  assert.equal(labels(crowded).length,1);
  const many=Array.from({length:30},(_,i)=>node('n'+i,Math.cos(i)*140,Math.sin(i)*140));
  assert(labels(many).length<=8);
});
test('labels disappear when pointer leaves and nothing is selected',()=>{
  assert.equal(labels([node('n',0,0)],{pointer:null}).length,0);
});
const fiberCode=source.slice(source.indexOf('function drawFiber('),source.indexOf('function drawNearbyLabels('));
function fibers(time,reduced=false,is3d=false){
  const gradients=[];
  const ctx={setLineDash(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},createLinearGradient(...points){const stops=[];gradients.push({points,stops});return {addColorStop(offset,color){assert(offset>=0&&offset<=1);stops.push([offset,color])}}}};
  const sandbox={ctx,hover:0,adj:[new Set([1])],cam:{k:1},RM:reduced,performance:{now:()=>time},N:[{x:0,y:0},{x:120,y:40},{x:240,y:80}],projected:[{x:10,y:20},{x:220,y:80},{x:300,y:90}],E:[{a:1,b:0},{a:1,b:2}]};
  vm.createContext(sandbox);vm.runInContext(fiberCode+`\ndrawFiber(${is3d});`,sandbox);return gradients;
}
test('fiber light moves along hovered connections in both projections',()=>{
  for(const is3d of [false,true]){
    const a=fibers(200,false,is3d),b=fibers(700,false,is3d);
    assert.equal(a.length,1);assert.notDeepEqual(a[0].stops,b[0].stops);
    assert.equal(a[0].points[0],is3d?10:0); // outward from the hovered node
    for(let i=1;i<a[0].stops.length;i++)assert(a[0].stops[i][0]>=a[0].stops[i-1][0]);
  }
});
test('reduced motion keeps the fiber steady',()=>{
  assert.deepEqual(fibers(200,true),fibers(700,true));
});
test('closing the reader keeps selection and animated connections',()=>{
  const code=source.slice(source.indexOf('function closeReader()'),source.indexOf('async function openNote('));
  const reader={classList:{remove(){}},setAttribute(){}};
  const sandbox={selected:0,focusSet:new Set([0,1]),needs:false,current:'file.md',$:()=>reader};
  vm.createContext(sandbox);vm.runInContext(code+'\ncloseReader();',sandbox);
  assert.equal(sandbox.selected,0);assert.equal(sandbox.focusSet.size,2);assert.equal(sandbox.current,null);
  const gradients=[];
  const ctx={setLineDash(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},createLinearGradient(){gradients.push(1);return {addColorStop(){}}}};
  Object.assign(sandbox,{ctx,hover:-1,adj:[new Set([1])],cam:{k:1},RM:false,performance:{now:()=>300},N:[{x:0,y:0},{x:100,y:40}],E:[{a:0,b:1}]});
  vm.runInContext(fiberCode+'\ndrawFiber(false);',sandbox);assert.equal(gradients.length,1);
});
test('isolated files are red and tags are yellow',()=>{
  const code=source.slice(source.indexOf('function nodeColor('),source.indexOf('function draw3d('));
  const sandbox={TONES:['normal']};vm.createContext(sandbox);vm.runInContext(code,sandbox);
  assert.equal(sandbox.nodeColor({isolated:true,kind:'codigo',c:0}),'#FF6B78');
  assert.equal(sandbox.nodeColor({kind:'tag',c:0}),'#F5C84B');
});

const dirtyCode=source.slice(source.indexOf('function editorDirty('),source.indexOf('function editorMessage('));
test('new drafts and edited content warn before leaving; clean notes do not',()=>{
  let confirmations=0;
  const sandbox={editorSession:{create:true,original:'template'},$:()=>({value:'template'}),confirm:()=>{confirmations++;return false},toast(){}};
  vm.createContext(sandbox);vm.runInContext(dirtyCode,sandbox);
  assert(sandbox.editorDirty());assert.equal(sandbox.canLeaveEditor(),false);assert.equal(confirmations,1);
  sandbox.editorSession.create=false;
  assert.equal(sandbox.editorDirty(),false);assert(sandbox.canLeaveEditor());assert.equal(confirmations,1);
  sandbox.$=()=>({value:'changed'});
  assert(sandbox.editorDirty());assert.equal(sandbox.canLeaveEditor(),false);
});
test('cannot leave while a save is in progress',()=>{
  const sandbox={editorSession:{saving:true,original:'saved'},$:()=>({value:'saved'}),confirm:()=>{throw Error('should not ask')},toast(){}};
  vm.createContext(sandbox);vm.runInContext(dirtyCode,sandbox);
  assert.equal(sandbox.canLeaveEditor(),false);
});

const wikiCode=source.slice(source.indexOf('function wikiAtCursor('),source.indexOf('function hideWiki('));
test('wiki completion respects cursor, selection and closed links',()=>{
 const sandbox={};vm.createContext(sandbox);vm.runInContext(wikiCode,sandbox);
 assert.equal(sandbox.wikiAtCursor('[[Nome]]',8),null);
 assert.equal(sandbox.wikiAtCursor('[[Nome',6,3),null);
 assert.equal(sandbox.wikiAtCursor('[[Nome|alias',12),null);
 assert.equal(sandbox.wikiAtCursor('[[Nome#parte',12),null);
 const span=sandbox.wikiAtCursor('Antes [[Pla]] depois',11);
 assert.equal(span.query,'Pla');assert.equal(span.from,6);assert.equal(span.to,13);
 const inserted='Antes [[Pla]] depois'.slice(0,span.from)+'[[b/Plano]]'+'Antes [[Pla]] depois'.slice(span.to);
 assert.equal(inserted,'Antes [[b/Plano]] depois');
 assert.equal(sandbox.wikiAtCursor('[[\n',3),null);
});
test('editing a wiki target preserves its heading and display alias',()=>{
 const sandbox={};vm.createContext(sandbox);vm.runInContext(wikiCode,sandbox);
 const span=sandbox.wikiAtCursor('[[Pl#Seção|Título]]',4);
 assert.equal(span.query,'Pl');assert.equal(span.suffix,'#Seção|Título');
 assert.equal('[[b/Plano'+span.suffix+']]','[[b/Plano#Seção|Título]]');
});

test('recent changes recolor files only when enabled and retain semantic colors',()=>{
 const code=source.slice(source.indexOf('function nodeColor('),source.indexOf('function draw3d('));
 const sandbox={TONES:['normal'],layers:{changes:false},changeFiles:new Map([['a','alterado']])};
 vm.createContext(sandbox);vm.runInContext(code,sandbox);
 const file={path:'a',kind:'codigo',c:0};
 assert.equal(sandbox.nodeColor(file),'normal');
 sandbox.layers.changes=true;
 assert.equal(sandbox.nodeColor(file),'#10B981');
 assert.equal(sandbox.nodeColor({...file,path:'b'}),'normal');
 assert.equal(sandbox.nodeColor({...file,isolated:true}),'#FF6B78');
 assert.equal(sandbox.nodeColor({...file,kind:'tag'}),'#F5C84B');
});
test('fiber does not paint when neither hover nor selection is active',()=>{
 const sandbox={hover:-1,selected:-1,ctx:{setLineDash(){throw Error('unexpected drawing')}},adj:[]};
 vm.createContext(sandbox);vm.runInContext(fiberCode,sandbox);sandbox.drawFiber(false);sandbox.drawFiber(true);
});
test('restoring saved positions initializes hover intensity so nodes stay visible',()=>{
 const code=source.slice(source.indexOf('  // posição inicial em espiral por comunidade:'),source.indexOf('  E=d.edges.filter'));
 const sandbox={N:[],nodes:[{id:'restored',kind:'codigo',label:'a',c:0,deg:1},{id:'existing',kind:'codigo',label:'b',c:0,deg:2}],old:new Map([['existing',{x:40,y:60,h:.4}]]),restoring:true,restoredPositions:new Map([['restored',{x:10,y:20}]])};
 vm.createContext(sandbox);vm.runInContext(code,sandbox);
 assert.equal(sandbox.N[0].h,0);assert.equal(sandbox.N[1].h,.4);
 for(const n of sandbox.N){n.h+=(0-n.h)*.25;assert(Number.isFinite(n.r*(1+n.h*.35)));}
});
