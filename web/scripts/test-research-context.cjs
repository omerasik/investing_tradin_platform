/* eslint-disable @typescript-eslint/no-require-imports -- Fixture-only isolated server rendering. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const symbols = ['BTCUSDT','ETHUSDT','SOLUSDT'];
const original = symbols.map((instrument,i) => ({instrument,manifest_hash:String(i+1).repeat(64),
  dataset_version_id:id(i+10),frame_kind:'T2_ARCHIVE_OHLCV_1M',label:'Misleading OTHERUSDT label',
  row_count:10,tier_ceiling:'NO_TIMING_AUTHORITY',timeframes:['1m','1d'],window:{content_hash:String(i+4).repeat(64),
    source_id:id(i+20),first_utc_day:'2026-06-01',last_utc_day:'2026-06-01',files:1,
    not_published_days:[],rejected_days:[],day_gaps:[],bar_logical_content_hash:'a'.repeat(64),last_bar_close_at:'2026-06-02T00:00:00Z'}}));
let catalogue = original;
let market = 'BTCUSDT';
const absent = {state:'UNAVAILABLE',detail:'Fixture unavailable'};
const calls = [];
const summary = () => ({study_id:id(1),instrument:market,instrument_source:'RESEARCH_WINDOW_CATALOGUE',
  label:'Misleading OTHERUSDT label',strategy_family:'trend_ma_cross',strategy_version:'1',planned_trial_count:1,
  queue_states:{},authority:{reasons:['SEARCH_ONLY']},content_hash:'e'.repeat(64),result_count:0,
  registered_at:'2026-06-01T00:00:00Z',evaluation_upper_bound_exclusive:'2026-08-20T00:00:00Z',
  datasets:original.filter(item=>item.instrument===market).map(item=>({role:'bars',dataset_version_id:item.dataset_version_id,
    content_hash:item.window.content_hash,resolution:'RESEARCH_WINDOW_CATALOGUE',symbol:market,evidence_tier:'T2_EVENT_TIME',
    first_utc_day:'2026-06-01',last_utc_day:'2026-06-01',not_published_days:[],rejected_days:[]}))});
const data = new Proxy({utc:value=>value,stateText:r=>r.detail??r.state,CHART_TIMEFRAMES:['1m','1d'],
  getWorkspaceContext:async()=>({evidenceTime:'2026-10-01T00:00:00Z'}),
  getChartSeriesCatalog:async()=>catalogue ? {state:'AVAILABLE',value:{state:'AVAILABLE',items:catalogue}} : absent,
  getChartCandles:async(_ctx,query)=>{calls.push(query);return absent;},
  getStrategyLabStudies:async()=>({state:'AVAILABLE',value:{state:'AVAILABLE',items:[summary()],total:1,
    page:{limit:50,offset:0,returned:1,has_more:true},facets:{instrument_catalogue:true,instruments:symbols,strategy_families:[]}}}),
  getStrategyLabStudy:async()=>({state:'AVAILABLE',value:{summary:summary(),manifests:[],candidate_sets:[],reruns:[],
    candidate_states:[],search:{},strategy:{},parameter_space:[{name:'fast',values:['1','2']},{name:'slow',values:['3','4']}],
    holdout:{state:'UNOPENED',boundary:'2026-08-20T00:00:00Z',cycle_id:'fixture',evaluation_before_boundary:true}}}),
  getStrategyLabTrials:async()=>({state:'AVAILABLE',value:{items:[{trial_id:id(3),ordinal:26,outcome:'EVALUATED',
    parameters:{},metrics:{total_return:'1'},queue_state:'SUCCEEDED',frozen_rank:1,rerun:null,candidate_state:null}],
    total:100,metric_keys:['total_return'],page:{limit:25,offset:25,returned:1,has_more:true}}}),
  getStrategyLabTrial:async()=>({state:'AVAILABLE',value:{trial:{trial_id:id(3),ordinal:1,outcome:'EVALUATED',parameters:{},metrics:{}},rerun_lags:[],candidate_states:[]}}),
}, {get:(target,key)=>key in target?target[key]:async()=>absent});
const load = Module._load;
Module._load = function(name,...args) {
  if (name==='../../lib/data-access'||name==='../lib/data-access') return data;
  if (name==='next/link') return {__esModule:true,default:({children,...props})=>React.createElement('a',props,children)};
  if (name==='./command-console') return {CommandConsole:()=>null};
  if (name==='../../components/market-chart') return {MarketChart:()=>null};
  return load.call(this,name,...args);
};
for (const extension of ['.tsx','.ts']) require.extensions[extension]=(module,file)=>module._compile(
  ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,
    module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
const {terminalQuery,terminalContext,strategyLabHref,candidateHref,researchChart}=require('../app/(protected)/terminal/compare-section.tsx');
const Terminal=require('../app/(protected)/terminal/page.tsx').default;
const Chart=require('../app/(protected)/chart/page.tsx').default;
const Lab=require('../app/(protected)/strategy-lab/page.tsx').default;
// Resolve async server children (TrialDetailPanel) without starting Next.js or any API.
async function resolve(node) {
  if (Array.isArray(node)) return Promise.all(node.map(resolve));
  if (!React.isValidElement(node)) return node;
  if (typeof node.type==='function' && node.type.constructor.name==='AsyncFunction') return resolve(await node.type(node.props));
  const children=await resolve(node.props.children);
  return Array.isArray(children) ? React.cloneElement(node,undefined,...children) : React.cloneElement(node,undefined,children);
}
const render=async(Page,params)=>renderToStaticMarkup(await resolve(await Page({searchParams:Promise.resolve(params)})));
const links=html=>[...html.matchAll(/<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g)]
  .map(([,href,text])=>({url:new URL(href.replaceAll('&amp;','&'),'http://fixture'),text}));
const selection={symbol:market,studies:[id(1),id(2)],metric:'total_return',sort:{dimension:'DECIMAL_BASELINE',metric:'total_return',direction:'DESC'},
  studyOffset:50,paperStudy:id(1),paperTrial:id(3)};
const raw=query=>Object.fromEntries([...query.keys()].map(key=>[key,query.getAll(key).length>1?query.getAll(key):query.get(key)]));
const assertContext=query=>{
  assert.deepEqual(query.getAll('compare_study'),selection.studies);
  assert.equal(query.get('compare_metric'),'total_return');
  assert.equal(query.get('compare_dimension'),'DECIMAL_BASELINE');
  assert.equal(query.get('compare_direction'),'DESC');
  assert.equal(query.get('study_offset'),'50');
  assert.equal(query.get('paper_trial'),id(3));
};
(async()=>{
  assert.equal(strategyLabHref(),'/strategy-lab');
  assert.equal(strategyLabHref(undefined,undefined,terminalContext('')),'/strategy-lab');
  for (const symbol of symbols) {
    market=symbol;catalogue=original;
    const context={...selection,symbol};
    const html=await render(Terminal,raw(terminalQuery(context)));
    const chartLink=links(html).find(link=>link.text===`Market Chart for ${symbol}`);
    assert.ok(chartLink);
    assert.equal(chartLink.url.searchParams.get('series'),original.find(item=>item.instrument===symbol).manifest_hash);
    assert.equal(chartLink.url.searchParams.get('symbol'),symbol);
    calls.length=0;
    const chart=await render(Chart,raw(chartLink.url.searchParams));
    assert.equal(calls.length,1);
    assert.equal(calls[0].manifestHash,chartLink.url.searchParams.get('series'));
    assert.match(chart,/HISTORICAL RESEARCH BARS: NOT LIVE PRICES/);
    const back=links(chart).find(link=>link.url.pathname==='/terminal');
    assertContext(back.url.searchParams);
    assert.equal(back.url.searchParams.get('symbol'),symbol);
    const timeframe=links(chart).find(link=>link.text==='1m');
    assertContext(terminalQuery(terminalContext(timeframe.url.searchParams.get('terminal_context'))));
    const labUrl=new URL(strategyLabHref(id(1),id(3),context),'http://fixture');
    const lab=await render(Lab,{...raw(labUrl.searchParams),sort:'total_return',dir:'desc',offset:'25',limit:'25'});
    const candidate=links(lab).find(link=>link.text==='Review candidate, risk and paper');
    assert.ok(candidate);
    assert.equal(candidate.url.hash,'#opportunity-detail');
    assert.equal(candidate.url.searchParams.get('paper_study'),id(1));
    assertContext(candidate.url.searchParams);
    const validation=links(lab).find(link=>link.text==='Validation status with this candidate selected');
    assert.equal(validation.url.hash,'#validation');assertContext(validation.url.searchParams);
    assertContext(links(lab).find(link=>link.text.includes('Validation &amp; Paper')||link.text.includes('Validation & Paper')).url.searchParams);
    const next=links(lab).find(link=>link.url.searchParams.get('offset')==='50');
    assert.ok(next);assertContext(terminalQuery(terminalContext(next.url.searchParams.get('terminal_context'))));
    assert.equal(next.url.searchParams.get('sort'),'total_return');
    assert.equal(next.url.searchParams.get('dir'),'desc');
    assert.equal(next.url.searchParams.get('instrument'),symbol);
    const carried=[...lab.matchAll(/name="terminal_context" value="([^"]*)"/g)];
    assert.ok(carried.length>=3);
    for (const [,value] of carried) assertContext(terminalQuery(terminalContext(value.replaceAll('&amp;','&'))));
    assert.match(lab,/Nothing here runs a search, opens the holdout/);
  }
  market='BTCUSDT';
  const empty=new URL(strategyLabHref(id(1),id(3),{...selection,studies:[]}),'http://fixture');
  const emptyLab=await render(Lab,raw(empty.searchParams));
  assert.deepEqual(links(emptyLab).find(link=>link.text==='Review candidate, risk and paper').url.searchParams.getAll('compare_study'),[]);
  const all={...selection,symbol:undefined,series:original[0].manifest_hash};
  const allChart=await render(Chart,{series:original[0].manifest_hash,terminal_context:terminalQuery(all).toString()});
  const allBack=links(allChart).find(link=>link.url.pathname==='/terminal');
  assertContext(allBack.url.searchParams);assert.equal(allBack.url.searchParams.has('symbol'),false);
  const allLab=await render(Lab,raw(new URL(strategyLabHref(id(1),id(3),all),'http://fixture').searchParams));
  const allCandidate=links(allLab).find(link=>link.text==='Review candidate, risk and paper');
  assertContext(allCandidate.url.searchParams);assert.equal(allCandidate.url.searchParams.has('symbol'),false);
  for (const bad of [undefined,[],original.filter(item=>item.instrument!=='SOLUSDT'),[...original,original[2]]]) {
    catalogue=bad;market='SOLUSDT';calls.length=0;
    const terminal=await render(Terminal,{symbol:market});
    assert.ok(!links(terminal).some(link=>link.url.pathname==='/chart'));
    assert.match(terminal,/unavailable|No exact|Ambiguous/i);
    await render(Chart,{symbol:market,series:original[2].manifest_hash});
    assert.equal(calls.length,0);
  }
  catalogue=original;calls.length=0;
  for (const request of [{series:'f'.repeat(64)},{symbol:'ETHUSDT',series:original[0].manifest_hash},{symbol:'INVALID'}, {series:['invalid']}]) {
    const html=await render(Chart,request);
    assert.match(html,/unavailable|Choose BTCUSDT/i);
    assert.equal(calls.length,0);
  }
  assert.equal(researchChart(original,'BTCUSDT',undefined,{dataset_version_id:id(10),content_hash:'f'.repeat(64)}).series,undefined);
  assert.equal(researchChart([{...original[0],dataset_version_id:''}],'BTCUSDT').series,undefined);
  assert.equal(researchChart([{...original[0],window:{...original[0].window,content_hash:''}}],'BTCUSDT').series,undefined);
  const unsafe='compare_study=bad&symbol=BTC&study_offset=10001&paper_study=bad&paper_trial=bad&compare_metric=%3Cscript%3E&redirect=https%3A%2F%2Fevil.test';
  assert.equal(terminalQuery(terminalContext(unsafe)).toString(),'');
  assert.deepEqual(terminalContext('x'.repeat(4097)).studies,[]);
  const roundtrip=new URL(candidateHref(id(1),id(3),terminalContext(new URL(strategyLabHref(id(1),id(3),selection),'http://fixture').searchParams.get('terminal_context'))),'http://fixture');
  assertContext(roundtrip.searchParams);
  console.log('Research context: BTC/ETH/SOL exact chart handoffs, refusal without fallback, catalogue identity mismatch, candidate/validation return links, paging and comparison context passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
