// Result chart declarations; application state is read only when called.
'use strict';
function populateChartColumns(){
 const columns=queryResult?.columns||[];
 for(const id of ['chart-x-col','chart-y-col'])$(id).replaceChildren(...columns.map((c,i)=>new Option(typeof c==='string'?c:c.name,String(i))));
 const numeric=columns.findIndex(c=>/^(tinyint|smallint|integer|int|bigint|real|double|decimal|numeric|float)\b/i.test(c.type||''));
 $('chart-x-col').value='0';$('chart-y-col').value=String(numeric>=0?numeric:columns.length>1?1:0);
}
function renderChart(){
 const container=$('query-chart');container.replaceChildren();if(!queryResult)return;
 const xi=Number($('chart-x-col').value),yi=Number($('chart-y-col').value);
 const points=visibleRows().filter(row=>row[yi]!==null&&row[yi]!==''&&['number','string'].includes(typeof row[yi])&&Number.isFinite(Number(row[yi])));
 if(!points.length){ui(container,'Choose a numeric Y column to chart visible rows.');return;}
 const ns='http://www.w3.org/2000/svg';
 const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 800 340');svg.setAttribute('role','img');svg.setAttribute('aria-label',t('Result chart'));
 function mark(tag,attrs,text){const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;svg.append(node);return node;}
 const values=points.map(row=>Number(row[yi]));
 let low=Math.min(0,...values),high=Math.max(0,...values);if(low===high)high=low+1;
 const y=value=>270-(value-low)/(high-low)*230,step=680/points.length,x=i=>80+step*(i+.5),zero=y(0);
 for(let i=0;i<=4;i++){const value=low+(high-low)*i/4;mark('line',{x1:80,x2:760,y1:y(value),y2:y(value),class:'chart-axis'});mark('text',{x:70,y:y(value)+4,'text-anchor':'end',class:'chart-label'},Number(value.toPrecision(4)).toLocaleString(language));}
 mark('line',{x1:80,x2:760,y1:zero,y2:zero,class:'chart-axis'});
 if($('chart-type').value==='line')mark('polyline',{points:values.map((value,i)=>x(i)+','+y(value)).join(' '),class:'chart-line'});
 points.forEach((row,i)=>{const value=Number(row[yi]),node=$('chart-type').value==='bar'?mark('rect',{x:x(i)-step*.36,y:Math.min(zero,y(value)),width:step*.72,height:Math.max(1,Math.abs(y(value)-zero)),class:'chart-mark'}):mark('circle',{cx:x(i),cy:y(value),r:3,class:'chart-mark'});
 const title=document.createElementNS(ns,'title');title.textContent=cellText(row[xi])+': '+cellText(row[yi]);node.append(title);
 if(i%Math.max(1,Math.ceil(points.length/8))===0)mark('text',{x:x(i),y:292,'text-anchor':'middle',class:'chart-label'},cellText(row[xi]).slice(0,18));});
 const name=i=>typeof queryResult.columns[i]==='string'?queryResult.columns[i]:queryResult.columns[i]?.name||'';
 mark('text',{x:420,y:325,'text-anchor':'middle',class:'chart-label'},name(xi));
 mark('text',{x:80,y:22,class:'chart-label'},name(yi));container.append(svg);
}
