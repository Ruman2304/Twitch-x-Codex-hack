// Run with: node tests/test_overlay_frontend.cjs
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();
function node(key){if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:''});return nodes.get(key);}
let status=200,payload,seen;
const ctx=vm.createContext({document:{querySelector:node},location:{pathname:'/overlay/live',hash:'#token=fixture-capability'},URLSearchParams,Map,Date,Math,String,console,setInterval(){},fetch:async(url,options)=>{seen={url,options};return {status,ok:status===200,json:async()=>({data:payload})}}});
const source=fs.readFileSync('apps/web/overlay.js','utf8');
vm.runInContext(source.replace('update();setInterval(update,1000);',''),ctx);
const render=()=>vm.runInContext('update()',ctx);
(async()=>{
  payload={demo_mode:false,session:{status:'live'},round:{id:'round',state:'voting',remaining_sec:60,options:[{number:1,title:'<img src=x onerror=alert(1)>'},{number:2,title:'Draw a mascot'}]},tallies:{1:1,2:2},attempt:null};
  await render();assert.equal(seen.url,'/api/v1/overlay/state');assert.equal(seen.options.credentials,'omit');assert.equal(seen.options.headers.Authorization,'Bearer fixture-capability');assert(!seen.url.includes('fixture-capability'));
  assert(node('#overlay-options').innerHTML.includes('&lt;img'));assert(!node('#overlay-options').innerHTML.includes('<img'));assert(node('#overlay-timer').textContent.includes('01:00'));
  payload.session.status='paused';await render();assert(node('#overlay-timer').textContent.startsWith('PAUSED'));
  payload.session.status='live';payload.round.state='resolved';payload.round.winner={number:2,title:'Draw a mascot'};payload.round.tallies={1:1,2:2};await render();assert.equal(node('#overlay-title').innerHTML,'Draw a mascot');
  payload.attempt={id:'attempt',round_id:'round',title:'Draw a mascot',instructions:'Use three shapes.',status:'running',remaining_sec:45};await render();assert.equal(node('#overlay-subtitle').textContent,'Use three shapes.');assert(node('#overlay-timer').textContent.includes('00:45'));
  payload.round={...payload.round,id:'next-round',state:'voting'};await render();assert.equal(node('#overlay-title').innerHTML,'Chat picks the next move.');
  status=503;await render();assert.equal(node('#overlay-options').innerHTML,'');assert.equal(node('#overlay-title').textContent,'Overlay unavailable');
  status=200;await render();assert(node('#overlay-options').innerHTML.includes('Draw a mascot'));
  status=401;await render();assert.equal(node('#overlay-options').innerHTML,'');assert.equal(node('#overlay-status').textContent,'LINK EXPIRED OR REVOKED');
  seen=null;await render();assert.equal(seen,null);
  console.log('PASS: token-only fetch, escaped text, voting, winner, attempt, pause, next round, outage recovery and revoked-link clearing');
})().catch(error=>{console.error(error);process.exitCode=1});
