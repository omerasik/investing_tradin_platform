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
const {CandidateJourney, candidateHref} = require('../app/(protected)/terminal/compare-section.tsx');
const selection = {studies: ['study-a', 'study-b'], metric: 'total_return', sort: {dimension: 'SEARCH', direction: 'DESC'}};
const url = new URL(candidateHref('study-a', 'trial-a', selection), 'http://localhost');
assert.deepEqual(url.searchParams.getAll('compare_study'), selection.studies);
assert.equal(url.searchParams.get('compare_metric'), 'total_return');
assert.equal(url.searchParams.get('compare_direction'), 'DESC');
assert.equal(url.searchParams.get('paper_trial'), 'trial-a');
assert.equal(url.hash, '#opportunity-detail');
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
    console.log('Candidate journey: context links, empty/unavailable states and BTC/ETH/SOL risk-to-paper browser checks passed.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
