/* eslint-disable @typescript-eslint/no-require-imports -- Isolated server render with fixture read models. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {chromium} = require('@playwright/test');
const ids = Array.from({length: 51}, (_, i) => `00000000-0000-0000-0000-${String(i + 1).padStart(12, '0')}`);
let mode = 'available';
let lastFilters;
let lastSignalTarget;
let signalMode = 'available';
let signalAccess;
const storedSignals = ['BTCUSDT','ETHUSDT','SOLUSDT'].flatMap((symbol,market) =>
  Array.from({length:51},(_,i) => ({signal_id:`signal-${symbol}-${i}`,symbol,study_id:ids[0],trial_id:ids[1],
    decided_at:'2026-06-01T00:00:00Z',target_from:0,target_to:1,claim:'NOT_VALIDATED_RESEARCH_WATCH',
    explanation:{stored_sequence:String(market*51+i)}}))).reverse();
const absent = {state:'UNAVAILABLE', detail:'Fixture evidence unavailable'};
const study = id => ({study_id:id,label:`Stored ${id}`,strategy_family:'trend',planned_trial_count:1,queue_states:{},authority:{reasons:['SEARCH_ONLY']},instrument:lastFilters?.instrument ?? 'BTCUSDT',instrument_source:'RESEARCH_WINDOW_CATALOGUE'});
const paper = {
  study_id:ids[0], trial_id:ids[1], cycle_id:'fixture', symbol:'BTCUSDT', as_of:'2026-10-23T00:00:00Z',
  claim:'PAPER_EVIDENCE_NOT_A_PROMOTION_DECISION',validated_claim:'NONE_VALIDATED',execution_authority:'NONE',
  parity:{status:'NOT_LOADED',loaded:false,reasons:[],live_side:{}},
  funding:{status:'NOT_LOADED',loaded:false,reasons:[],cost_status:'NON_COSTABLE',completeness_blockers:[],economic_blockers:[]},
  economics:{status:'NOT_LOADED',reasons:[],sensitivity_rows:[]},
  risk:{status:'UNRESOLVED',claim:'PAPER_RISK_ONLY',reasons:['MISSING_OWNER_POLICY'],controls:[],sizing_absent_reasons:['MISSING_OWNER_POLICY']},
};
const data = new Proxy({
  utc: value => value, stateText: result => result.detail ?? result.state,
  getWorkspaceContext: async () => ({origin:'http://terminal.test',protectedApi:true,evidenceTime:'2026-10-23T00:00:00Z'}),
  getTerminalSignals: async (ctx,symbol) => signalAccess(ctx,symbol),
  getStrategyLabStudy: async (_ctx,id) => ({state:'AVAILABLE',value:{summary:study(id)}}),
  getTerminalPaperEvidence: async () => ({state:'AVAILABLE',value:{...paper,symbol:lastFilters?.instrument ?? 'BTCUSDT'}}),
  getStrategyLabStudies: async (_ctx,filters) => {
    lastFilters = filters;
    if (mode === 'unavailable') return absent;
    const items = mode === 'empty' ? [] : ids.slice(filters.offset, filters.offset + filters.limit).map(study);
    return {state:'AVAILABLE',value:{state:mode === 'inner-unavailable' ? 'UNAVAILABLE':'AVAILABLE',items,
      page:{limit:50,offset:filters.offset,returned:items.length,has_more:mode === 'available' && filters.offset === 0},
      total:null,facets:{instrument_catalogue:true}}};
  },
}, {get:(target,key) => key in target ? target[key] : async () => absent});
const load = Module._load;
Module._load = function(id,...args) {
  if (id === '../../lib/data-access' || id === '../lib/data-access') return data;
  // Client command UI is outside this read-only pagination test.
  if (id === './command-console') return {CommandConsole:() => null};
  if (id === '../../components/research-workflow-nav') return {ResearchWorkflowNav:() => null};
  return load.call(this,id,...args);
};
for (const extension of ['.tsx','.ts']) require.extensions[extension] = (module,file) => {
  module._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
};
const Terminal = require('../app/(protected)/terminal/page.tsx').default;
// Exercise the actual data-access URL builder and EvidenceResult mapping, then the complete terminal render.
signalAccess = require('../app/lib/data-access.ts').getTerminalSignals;
const {allowedAuthorityTarget} = require('../app/authority-targets.ts');
global.fetch = async url => {
  lastSignalTarget = new URL(new URL(url).searchParams.get('target'),'http://authority.test');
  assert.equal(allowedAuthorityTarget(lastSignalTarget.pathname+lastSignalTarget.search),true);
  assert.equal(lastSignalTarget.searchParams.get('limit'),'50');
  if (signalMode === 'unavailable') return new Response('{}',{status:503});
  const symbol = lastSignalTarget.searchParams.get('symbol');
  const items = signalMode === 'empty' ? [] : storedSignals.filter(item => !symbol || item.symbol===symbol).slice(0,50);
  return Response.json(items);
};
const {studyOffset,StudyPagination} = require('../app/(protected)/terminal/study-pagination.tsx');
const {candidateHref,terminalQuery} = require('../app/(protected)/terminal/compare-section.tsx');
for (const value of [undefined,'-1','1.5','1e3','10001','9999999999']) assert.equal(studyOffset(value),0);
assert.equal(studyOffset('10000'),10000);
const selection = {symbol:'BTCUSDT',studies:[ids[0]],metric:'total_return',sort:{dimension:'SEARCH',metric:'total_return',direction:'DESC'},paperStudy:ids[0],paperTrial:ids[1],studyOffset:50};
assert.equal(new URL(candidateHref(ids[0],ids[1],selection),'http://fixture').searchParams.get('study_offset'),'50');
assert.equal(terminalQuery(selection).get('paper_trial'),ids[1]);
const initial = new URL('/terminal?' + terminalQuery({...selection,studyOffset:0}),'http://terminal.test');
const render = async url => {
  const raw = {};
  for (const key of url.searchParams.keys()) {const values=url.searchParams.getAll(key);raw[key]=values.length>1?values:values[0];}
  return renderToStaticMarkup(await Terminal({searchParams:Promise.resolve(raw)}));
};
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.route('http://terminal.test/**',async route => route.fulfill({contentType:'text/html; charset=utf-8',body:await render(new URL(route.request().url()))}));
    await page.goto(initial.href);
    await page.getByRole('checkbox').nth(1).check();
    await page.locator('select[name=compare_dimension]').selectOption('DECIMAL_BASELINE');
    await page.getByRole('button',{name:'Next study page',exact:true}).click();
    assert.deepEqual(new URL(page.url()).searchParams.getAll('compare_study'),[ids[0],ids[1]]);
    assert.equal(new URL(page.url()).searchParams.get('compare_dimension'),'DECIMAL_BASELINE');
    for (const symbol of ['BTCUSDT','ETHUSDT','SOLUSDT']) {
      initial.searchParams.set('symbol',symbol);
      await page.goto(initial.href);
      assert.equal(lastFilters.instrument,symbol);
      assert.equal(lastSignalTarget.searchParams.get('symbol'),symbol);
      assert.equal(await page.locator('#opportunities tbody tr').count(),50);
      assert.match(await page.locator('#opportunities').textContent(),new RegExp(symbol));
      const candidate = page.getByRole('link',{name:'Review candidate, risk and paper',exact:true}).first();
      const candidateUrl = new URL(await candidate.getAttribute('href'),'http://terminal.test');
      assert.equal(candidateUrl.searchParams.get('symbol'),symbol);
      assert.deepEqual(candidateUrl.searchParams.getAll('compare_study'),[ids[0]]);
      assert.equal(candidateUrl.searchParams.get('compare_metric'),'total_return');
      assert.equal(candidateUrl.searchParams.get('compare_dimension'),'SEARCH');
      assert.equal(candidateUrl.searchParams.get('compare_direction'),'DESC');
      assert.equal(candidateUrl.searchParams.get('paper_study'),ids[0]);
      assert.equal(candidateUrl.searchParams.get('paper_trial'),ids[1]);
      await candidate.click();
      await page.getByRole('link',{name:'Review risk',exact:true}).click();
      assert.match(await page.locator('#selected-risk').textContent(),/MISSING_OWNER_POLICY/);
      await page.getByRole('link',{name:'Review paper evidence',exact:true}).click();
      assert.match(await page.locator('#selected-paper').textContent(),/NOT_LOADED/);
      await page.goto(initial.href);
      assert.equal(lastFilters.limit,50);
      const next = page.getByRole('button',{name:'Next study page',exact:true});
      await next.click();
      assert.equal(lastFilters.offset,50);
      assert.equal(new URL(page.url()).searchParams.get('symbol'),symbol);
      assert.equal(await page.locator('input[type=hidden][name=compare_study]').count(),1, await page.locator('#compare').textContent()); assert.equal(await page.locator('input[type=hidden][name=compare_study]').inputValue(),ids[0]);
      await page.getByRole('checkbox').check();
      await page.getByRole('button',{name:'Compare selected studies',exact:true}).click();
      assert.equal(lastFilters.offset,50);
      assert.deepEqual(new URL(page.url()).searchParams.getAll('compare_study'),[ids[0],ids[50]]);
      await page.getByRole('button',{name:'Previous studies, retaining selection'}).click();
      assert.equal(lastFilters.offset,0);
      assert.deepEqual(new URL(page.url()).searchParams.getAll('compare_study'),[ids[0],ids[50]]);
      assert.equal(new URL(page.url()).searchParams.get('compare_metric'),'total_return');
      assert.equal(new URL(page.url()).searchParams.get('compare_dimension'),'SEARCH');
      assert.equal(new URL(page.url()).searchParams.get('compare_direction'),'DESC');
      assert.equal(new URL(page.url()).searchParams.get('paper_trial'),ids[1]);
    }
    initial.searchParams.set('symbol','BTCUSDT');
    await page.goto(initial.href);
    await page.getByRole('link',{name:'Review risk',exact:true}).click();
    assert.match(await page.locator('#selected-risk').textContent(),/MISSING_OWNER_POLICY/);
    await page.getByRole('link',{name:'Review paper evidence',exact:true}).click();
    assert.match(await page.locator('#selected-paper').textContent(),/NOT_LOADED/);
    initial.searchParams.delete('symbol');
    await page.goto(initial.href);
    assert.equal(lastSignalTarget.searchParams.has('symbol'),false);
    assert.equal(await page.locator('#opportunities tbody tr').count(),50);
    initial.searchParams.set('symbol','DOGEUSDT');
    await page.goto(initial.href);
    assert.equal(lastSignalTarget.searchParams.has('symbol'),false);
    initial.searchParams.delete('symbol');
    await page.getByRole('button',{name:'Next study page',exact:true}).click();
    assert.equal(lastFilters.instrument,undefined);
    assert.equal(lastFilters.offset,50);
    assert.equal(await page.locator('input[type=hidden][name=compare_study]').inputValue(),ids[0]);
    initial.searchParams.set('symbol','BTCUSDT');
    initial.searchParams.set('study_offset','100');
    await page.goto(initial.href);
    assert.match(await page.locator('#strategy-lab').textContent(),/out of range/);
    await page.getByRole('button',{name:'First study page'}).click();
    assert.equal(lastFilters.offset,0);
    initial.searchParams.set('study_offset','10001');
    await page.goto(initial.href);
    assert.equal(lastFilters.offset,0);
    assert.match(await page.locator('#strategy-lab').textContent(),/Invalid or unsupported study offset/);
    for (const state of ['empty','unavailable','inner-unavailable']) {
      mode=state;
      initial.searchParams.set('study_offset','0'); initial.searchParams.set('fixture_state',state); await page.goto(initial.href);
      assert.equal(await page.getByRole('button',{name:'Next study page',exact:true}).count(),0);
      assert.match(await page.locator('#strategy-lab').textContent(),state==='empty'?/No catalogue-associated study/:/unavailable/i);
      assert.equal(await page.locator('input[type=hidden][name=compare_study]').count(),1, await page.locator('#compare').textContent()); assert.equal(await page.locator('input[type=hidden][name=compare_study]').inputValue(),ids[0]);
    }
    for (const state of ['empty','unavailable']) {
      signalMode=state;
      await page.goto(initial.href);
      assert.equal(await page.locator('#opportunities tbody tr').count(),0);
      assert.match(await page.locator('#opportunities').textContent(),state==='empty'?/No live signal recorded/:/Evidence source responded 503/);
      if (state==='unavailable') assert.doesNotMatch(await page.locator('#opportunities').textContent(),/No live signal recorded/);
    }
    await page.setContent(renderToStaticMarkup(React.createElement(StudyPagination,{selection,page:{limit:50,offset:10000,returned:50,has_more:true}})));
    assert.equal(await page.getByRole('button',{name:'Next study page',exact:true}).count(),0);
    assert.match(await page.textContent('body'),/offset limit is reached/);
    console.log('Terminal pagination and signals: actual scoped data-access, 153 mixed-symbol signals, all markets, invalid UI symbol, candidate/risk/paper links, study pagination and selections, distinct empty/unavailable states passed.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
