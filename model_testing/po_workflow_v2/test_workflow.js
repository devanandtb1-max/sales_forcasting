const fs=require('fs'), path=require('path'), assert=require('assert/strict');
const root=__dirname;
const read=n=>fs.readFileSync(path.join(root,n),'utf8');
const payload=JSON.parse(fs.readFileSync(path.join(root,'../runs/2f8a0f1369b14f7ea4c0c183194246ee/payload.json'),'utf8'));
const config={store:'Pearcedale',as_of_date:'2026-09-16',expected_sku_count:99,expected_sku_ids:[]};
const run=(file,json)=>new Function('$input','$','$execution','Buffer',read(file))({first:()=>({json})},()=>({first:()=>({json:config})}),{id:'local-test'},Buffer);
let count=0;
function test(name,fn){fn();count++;console.log('PASS '+name);}
const normalize=p=>run('normalize_modal.js',p)[0].json;
test('full real sample accepted',()=>assert.equal(normalize(payload).status,'passed'));
test('HTTP envelope accepted',()=>assert.equal(normalize({statusCode:200,body:payload}).status,'passed'));
for(const [name,mutate] of [
 ['negative quantity',p=>p.recommendations[0].recommended_order_qty=-1],
 ['missing quantity',p=>p.recommendations[0].recommended_order_qty=null],
 ['duplicate forecast',p=>p.forecasts[1]=p.forecasts[0]],
 ['missing recommendation',p=>p.recommendations.pop()],
 ['wrong horizon',p=>p.forecasts[0].horizon_end='2026-10-02'],
 ['wrong summary',p=>p.summary.total_skus=100],
 ['invalid row',p=>p.forecasts[0]=null],
 ['wrong decision',p=>p.recommendations[0].po_decision='APPROVE'],
 ['missing inventory flag',p=>delete p.inventory_is_simulated],
 ['mismatched forecast',p=>p.recommendations[0].hybrid_forecast_15d=999999],
]) test(name,()=>{const p=structuredClone(payload);mutate(p);assert.equal(normalize(p).status,'failed');});
for(const p of [null,{}, {statusCode:500}, {body:'not json'},{body:[]},{status:'error'}, {error:'connection failed'}])
 test('malformed or error response rejected',()=>assert.equal(normalize(p).status,'failed'));
const reviewed=run('build_review.js',normalize(payload))[0].json;
test('review retains all rows and model quantities',()=>{assert.equal(reviewed.recommendations.length,99);for(const r of reviewed.recommendations){assert.equal(r.recommended_order_qty,payload.recommendations.find(p=>p.itemCode===r.sku).recommended_order_qty);assert.equal(r.submission_allowed,false);}assert.equal(reviewed.recommendations[0].stockout_risk,'Critical');});
test('all three complete downloads generated',()=>{const out=run('export_results.js',reviewed)[0];assert.equal(Object.keys(out.binary).length,3);assert.equal(JSON.parse(Buffer.from(out.binary.run_json.data,'base64')).forecasts.length,99);for(const k of ['forecasts_csv','po_review_csv'])assert.equal(Buffer.from(out.binary[k].data,'base64').toString().split('\r\n').length,100);});
test('CSV formula neutralization',()=>{const p=structuredClone(reviewed);p.forecasts[0].category='=1+1';const out=run('export_results.js',p)[0];assert.ok(Buffer.from(out.binary.forecasts_csv.data,'base64').toString().includes("\"'=1+1\""));});
test('workflow graph and JS syntax',()=>{for(const file of ['n8n_po_workflow_v2.json','n8n_po_storage_v2.json']){const w=JSON.parse(read(file));assert.equal(w.active,false);const names=new Set(w.nodes.map(n=>n.name));for(const [from,c] of Object.entries(w.connections)){assert.ok(names.has(from));for(const output of c.main)for(const e of output)assert.ok(names.has(e.node));}for(const n of w.nodes)if(n.type.endsWith('.code'))new Function('$input','$','$execution','Buffer',n.parameters.jsCode);}});
console.log(`${count} tests passed. No cloud request or database write performed.`);
