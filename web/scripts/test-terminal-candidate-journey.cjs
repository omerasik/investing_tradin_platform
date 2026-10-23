/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS loader hooks compile isolated server components for browser assertions. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {chromium} = require('@playwright/test');
// Render only the server component; no runtime, credentials or evidence service is contacted.
const load = Module._load;
Module._load = function(id, ...args) {
  if (id === '../../lib/data-access') return {utc: value => value};
  return load.call(this, id, ...args);
};
for (const extension of ['.tsx', '.ts']) require.extensions[extension] = (module, file) => {
  module._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }}).outputText, file);
};
const {CandidateJourney, candidateHref, ComparisonSection} = require('../app/(protected)/terminal/compare-section.tsx');
const {SymbolNavigation, terminalSymbol, matchesSymbol} = require('../app/(protected)/terminal/symbol-navigation.tsx');
assert.equal(terminalSymbol('BTCUSDT'), 'BTCUSDT');
assert.equal(terminalSymbol('BTC'), undefined);
assert.equal(terminalSymbol(['ETHUSDT']), undefined);
assert.equal(matchesSymbol({instrument:'BTCUSDT',instrument_source:'UNRESOLVED',label:'BTCUSDT'},'BTCUSDT'),false);
assert.equal(matchesSymbol({instrument:'ETHUSDT',instrument_source:'RESEARCH_WINDOW_CATALOGUE'},'BTCUSDT'),false);
assert.equal(matchesSymbol({instrument:'BTCUSDT',instrument_source:'RESEARCH_WINDOW_CATALOGUE'},'BTCUSDT'),true);
const selection = {symbol:'BTCUSDT',studies: ['study-a', 'study-b'], metric: 'total_return', sort: {dimension: 'SEARCH', direction: 'DESC'}};
const url = new URL(candidateHref('study-a', 'trial-a', selection), 'http://localhost');
assert.deepEqual(url.searchParams.getAll('compare_study'), selection.studies);
assert.equal(url.searchParams.get('compare_metric'), 'total_return');
assert.equal(url.searchParams.get('compare_direction'), 'DESC');
assert.equal(url.searchParams.get('paper_trial'), 'trial-a');
assert.equal(url.hash, '#opportunity-detail');
assert.equal(url.searchParams.get('symbol'), 'BTCUSDT');
const absent = {status:'NOT_LOADED', loaded:false, reasons:['NO_STORED_EVIDENCE'], live_side:{}};
const item = {
  study_id:'study-a', trial_id:'trial-a', cycle_id:'cycle-a', symbol:'BTCUSDT', as_of:'2026-10-23T00:00:00Z',
  claim:'PAPER_EVIDENCE_NOT_A_PROMOTION_DECISION', validated_claim:'NONE_VALIDATED', execution_authority:'NONE',
  parity:absent, funding:{...absent,cost_status:'NON_COSTABLE',completeness_blockers:[],economic_blockers:[]},
  economics:{status:'NOT_LOADED',reasons:['NO_ECONOMICS'],sensitivity_rows:[]},
  risk:{status:'UNRESOLVED',claim:'PAPER_RISK_ONLY',reasons:['MISSING_OWNER_POLICY'],controls:[],sizing_absent_reasons:['MISSING_OWNER_POLICY']},
};
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    for (const symbol of ['BTCUSDT','ETHUSDT','SOLUSDT']) {
      await page.setContent(renderToStaticMarkup(React.createElement(SymbolNavigation,{symbol,catalogue:true})));
      assert.equal(await page.getByRole('link',{name:symbol,exact:true}).getAttribute('aria-current'),'page');
      assert.equal(await page.getByRole('link',{name:symbol,exact:true}).getAttribute('href'),`/terminal?symbol=${symbol}#strategy-lab`);
      await page.setContent(renderToStaticMarkup(React.createElement(ComparisonSection,{
        studies:[{study_id:'study-a',label:'Stored study',strategy_family:'trend'}],
        selection:{...selection,symbol},unavailable:undefined,comparison:undefined,
      })));
      assert.equal(await page.locator('input[name=symbol]').inputValue(),symbol);
      assert.equal(await page.locator('input[type=hidden][name=compare_study]').inputValue(),'study-b');
      assert.equal(await page.getByRole('checkbox').isChecked(),true);
    }
    await page.setContent(renderToStaticMarkup(React.createElement(SymbolNavigation,{symbol:'BTCUSDT',catalogue:false})));
    assert.match(await page.textContent('body'),/no research window catalogue/);
    await page.setContent(renderToStaticMarkup(React.createElement(SymbolNavigation,{unavailable:'Research service unavailable'})));
    assert.match(await page.textContent('body'),/Research service unavailable/);
    await page.setContent(renderToStaticMarkup(React.createElement(ComparisonSection,{studies:[],selection:{studies:[],symbol:'SOLUSDT'},comparison:undefined,studiesUnavailable:'No catalogue-associated study recorded for SOLUSDT.'})));
    assert.match(await page.textContent('body'),/No catalogue-associated study/);
    await page.setContent(renderToStaticMarkup(React.createElement(CandidateJourney,{selected:false})));
    assert.match(await page.textContent('body'), /Compare studies/);
    await page.setContent(renderToStaticMarkup(React.createElement(CandidateJourney,{selected:true, unavailable:'Access unavailable'})));
    assert.match(await page.textContent('body'), /Access unavailable/);
    assert.equal(await page.locator('#selected-risk').count(),0);
    for (const symbol of ['BTCUSDT','ETHUSDT','SOLUSDT']) {
      await page.setContent(renderToStaticMarkup(React.createElement(CandidateJourney,{selected:true,item:{...item,symbol}})));
      await page.getByRole('link',{name:'Review risk',exact:true}).click();
      assert.match(await page.locator('#selected-risk').textContent(),/UNRESOLVED.*MISSING_OWNER_POLICY/s);
      await page.getByRole('link',{name:'Review paper evidence',exact:true}).click();
      assert.match(await page.locator('#selected-paper').textContent(),/NOT_LOADED/);
      assert.match(await page.textContent('body'),/NONE_VALIDATED/);
      assert.match(await page.textContent('body'),new RegExp(symbol));
    }
    console.log('Symbol navigation and candidate journey: catalogue associations, form context, empty/unavailable markets, context links, empty/unavailable states and BTC/ETH/SOL risk-to-paper browser checks passed.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
