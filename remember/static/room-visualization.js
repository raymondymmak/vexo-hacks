/* Isometric room adapted from claude/remember-web-app's floor, furniture and orb
   design. Uses real room participants and edges with explicitly illustrative positions. */
(() => {
  const NS='http://www.w3.org/2000/svg';
  const make=(tag,attrs={},text)=>{const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=text;return n;};
  const points=ps=>ps.map(p=>p.join(',')).join(' ');
  let previous=new Set(),lastData=null;
  window.renderAfterHelloRoom=(root,people,edges)=>{
    lastData={root,people,edges};
    const width=Math.max(300,root.clientWidth),mobile=width<600,height=mobile?360:520;
    root.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const n=Math.max(8,Math.ceil(Math.sqrt(people.length*2+12))),tw=Math.min((width-44)/n,(height-165)/(n*.5)),th=tw/2;
    const top=mobile?105:125,iso=(c,r,z=0)=>[width/2+(c-r)*tw/2,top+(c+r)*th/2-z];
    const nodes=[],defs=make('defs');
    [['ah-floor','#ffffff','#eef0f5'],['ah-orb','#7bbaff','#007aff'],['ah-real','#72c9b7','#176b61']].forEach(([id,a,b])=>{const g=make('linearGradient',{id,x1:0,y1:0,x2:1,y2:1});g.append(make('stop',{offset:0,'stop-color':a}),make('stop',{offset:1,'stop-color':b}));defs.append(g);});
    nodes.push(defs);
    const poly=(ps,fill,extra={})=>nodes.push(make('polygon',{points:points(ps),fill,...extra}));
    const A=iso(0,0),B=iso(n,0),C=iso(n,n),D=iso(0,n),down=p=>[p[0],p[1]+14];
    nodes.push(make('ellipse',{cx:width/2,cy:C[1]+14,rx:n*tw*.38,ry:18,fill:'#1d1d1f',opacity:.055}));
    poly([D,C,down(C),down(D)],'#d9dde6');poly([C,B,down(B),down(C)],'#c7ccd7');poly([A,B,C,D],'url(#ah-floor)');
    for(let c=0;c<n;c++)for(let r=0;r<n;r++)poly([iso(c,r),iso(c+1,r),iso(c+1,r+1),iso(c,r+1)],(c+r)%2?'#ffffff40':'transparent',{stroke:'#e0e4ed','stroke-width':.7,class:'room-tile'});
    function box(c,r,c1,r1,h,color){const a=iso(c,r),b=iso(c1,r),d=iso(c,r1),e=iso(c1,r1),up=p=>[p[0],p[1]-h];poly([up(d),up(e),e,d],'#ccd2dd');poly([up(e),up(b),b,e],'#b9c1cf');poly([up(a),up(b),up(e),up(d)],color,{class:'room-furniture'});}
    // Decorative stage and welcome desk, not measured venue furniture.
    box(.3,.3,2.3,1.2,tw*.22,'#e3eafa');box(n-2,.5,n-.4,1.2,tw*.4,'#d8ebe5');
    const table=iso(n/2,n/2),tableH=tw*.25;
    nodes.push(make('rect',{x:table[0]-tw*.2,y:table[1]-tableH,width:tw*.4,height:tableH,fill:'#ccd2dd'}),make('ellipse',{cx:table[0],cy:table[1]-tableH,rx:tw*.3,ry:tw*.15,fill:'#fff',stroke:'#dbe0e9',class:'room-furniture'}));
    const taken=new Set(),positions=new Map(),anchors=new Map();
    const ordered=[...people].sort((a,b)=>String(a.id).localeCompare(String(b.id)));
    ordered.forEach((p,i)=>{const angle=-Math.PI/2+2*Math.PI*i/Math.max(1,ordered.length);let c=Math.round((n-1)/2+Math.cos(angle)*(n/2-1.6)),r=Math.round((n-1)/2+Math.sin(angle)*(n/2-1.6));while(taken.has(`${c},${r}`)){c=(c+1)%n;if(!c)r=(r+1)%n;}taken.add(`${c},${r}`);positions.set(p.id,[c+.5,r+.5]);});
    ordered.sort((a,b)=>positions.get(a.id).reduce((x,y)=>x+y)-positions.get(b.id).reduce((x,y)=>x+y)).forEach(p=>{
      const [c,r]=positions.get(p.id),base=iso(c,r),lift=mobile?42:Math.min(76,tw*.85),o=iso(c,r,lift),rad=mobile?16:22;
      anchors.set(p.id,o);
      nodes.push(make('ellipse',{cx:base[0],cy:base[1],rx:tw*.3,ry:th*.3,fill:p.simulated?'#007aff18':'#176b6118',stroke:p.simulated?'#b0d5ff':'#94d1c4'}),make('path',{d:`M ${base[0]} ${base[1]} L ${o[0]} ${o[1]}`,stroke:p.simulated?'#91c4ff':'#87c6b8','stroke-width':5,opacity:.45}));
      const g=make('g',{class:'room-orb','data-person':p.id});g.append(make('title',{},p.name+(p.simulated?' · Simulated participant':' · Registered participant')),make('circle',{cx:o[0],cy:o[1]+3,r:rad+2,fill:'#000',opacity:.06}),make('circle',{cx:o[0],cy:o[1],r:rad,fill:p.simulated?'url(#ah-orb)':'url(#ah-real)',stroke:'#fff','stroke-width':2.5}),make('text',{x:o[0],y:o[1]+5,'text-anchor':'middle',fill:'#fff','font-size':mobile?12:15,'font-weight':600,class:'room-orb-initials'},p.name.split(/\s+/).map(n=>n[0]).slice(0,2).join('')));
      nodes.push(g);
    });
    const colors={meeting:'#8e9aaf',intro:'#007aff',match:'#176b61'},pairCounts=new Map(),ids=new Set();
    edges.forEach(e=>{const a=anchors.get(e.source_id),b=anchors.get(e.target_id);if(!a||!b)return;const key=[e.source_id,e.target_id].sort().join('|'),count=pairCounts.get(key)||0;pairCounts.set(key,count+1);const lift=Math.min(70,Math.hypot(b[0]-a[0],b[1]-a[1])*.3)+count*18;const path=make('path',{d:`M ${a[0]} ${a[1]} Q ${(a[0]+b[0])/2} ${(a[1]+b[1])/2-lift} ${b[0]} ${b[1]}`,fill:'none',stroke:colors[e.kind]||'#8e9aaf','stroke-width':2.3,'stroke-dasharray':e.kind==='intro'?'3 6':e.kind==='match'?'8 5':'none',class:'room-arc'+(previous.size&&!previous.has(String(e.id))?' room-arc-new':'')});path.append(make('title',{},e.kind));nodes.push(path);ids.add(String(e.id));});
    ordered.forEach(p=>{const o=anchors.get(p.id);nodes.push(make('text',{x:o[0],y:o[1]+(mobile?34:42),'text-anchor':'middle',fill:'#30333b','font-size':mobile?11:13,'font-weight':500,class:'room-name',stroke:'#fff','stroke-width':4,'paint-order':'stroke'},p.name));});
    if(!people.length)nodes.push(make('text',{x:width/2,y:75,'text-anchor':'middle',fill:'#86868b','font-size':14},'Ready for your first hello.'));
    root.replaceChildren(...nodes);previous=ids;
  };
  let timer;window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(()=>{if(lastData&&lastData.root.clientWidth)window.renderAfterHelloRoom(lastData.root,lastData.people,lastData.edges);},100);});
})();
