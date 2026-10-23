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
const {CandidateJourney, candidateHref, ComparisonSection, CostFragilityCell} = require('../app/(protected)/terminal/compare-section.tsx');
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
    const fragility = {
      status:'POSITIVE_GROSS_HEADROOM',threshold_bps_per_side:'2.500000000000000001',
      search_threshold_bps_per_side:'99',lag_micros:2000000,verified_account_fees:'UNAVAILABLE',
      net_profitability:'UNAVAILABLE',observed_spread_comparison:'UNAVAILABLE_NO_CANDIDATE_AND_WINDOW_BOUND_SPREAD_EVIDENCE',
      scenarios:[{name:'conservative_medium_2_5bps',cost_bps_per_side:'2.5',relation:'ABOVE',
        remaining_bps_per_side:'0.000000000000000001',status:'PROPOSED_SENSITIVITY_SCENARIO',source:'prepared proposal'}],
      reasons:['MISSING_VERIFIED_ACCOUNT_FEES'],
    };
    for (const assessment of [fragility,{...fragility,status:'UNAVAILABLE',threshold_bps_per_side:null,
      scenarios:[{...fragility.scenarios[0],remaining_bps_per_side:null,relation:'UNAVAILABLE'}]},undefined]) {
      await page.setContent(renderToStaticMarkup(React.createElement(CostFragilityCell,{row:{
        identity:{trial_id:'exact-candidate-id'},decimal_rerun:{rerun_hash:'rerun-hash'},
        provenance:{evaluation_upper_bound_exclusive:'2026-08-19T00:00:00+00:00',holdout_boundary:'2026-08-20T00:00:00+00:00'},
        cost_fragility:assessment,
      }})));
      if (assessment) {
        assert.equal(await page.locator('[data-cost-fragility]').getAttribute('data-cost-fragility'),'exact-candidate-id');
        await page.getByText('Proposed slippage sensitivity and evidence limits',{exact:true}).click();
        const text = await page.textContent('body');
        assert.match(text,/Fees, observed spread and funding are excluded/);
        assert.match(text,/No net profitability or independent validation is inferred/);
        assert.match(text,/2026-08-19T00:00:00\+00:00/);
        if (assessment.status === 'POSITIVE_GROSS_HEADROOM') {
          assert.match(text,/2\.500000000000000001/);
          assert.match(text,/0\.000000000000000001/);
        } else assert.match(text,/remainder unavailable bps\/side/);
      } else assert.equal(await page.textContent('body'),'UNAVAILABLE');
    }
    const feeAssessment = {...fragility, verified_account_fees:'VERIFIED',
      account_fee_evidence:{status:'VERIFIED',reasons:[],evidence_hash:'fixture-evidence-identity',
        taker_fee_bps_per_side:'0.123456789012345678901234567890123456789',
        remaining_after_fee_bps_per_side:'2.376543210987654322098765432109876543211',
        context:{account_id:'fixture-account',venue:'BYBIT',product:'USDT_PERPETUAL',tier:'fixture-vip',
          observation_start:'2025-08-20T00:00:00Z',observation_end_exclusive:'2026-08-19T00:00:00Z',assessed_at:'2026-10-23T00:00:00Z'},
        evidence:{verification_source:'ACTUAL_ACCOUNT_FEE_STATEMENT',valid_from:'2025-01-01T00:00:00Z',
          valid_until_exclusive:'2026-09-01T00:00:00Z',fee_schedule:{verified_by:'fixture-verifier',
            verified_on:'2026-10-01',source_reference:'fixture-statement'}}},
      scenarios:[{...fragility.scenarios[0],remaining_after_verified_fee_and_scenario_bps_per_side:'-0.123456789012345677901234567890123456789'}]};
    for (const verified of [true,false]) {
      const assessment = verified ? feeAssessment : {...feeAssessment,verified_account_fees:'UNAVAILABLE',
        account_fee_evidence:{...feeAssessment.account_fee_evidence,status:'UNAVAILABLE',
          taker_fee_bps_per_side:null,remaining_after_fee_bps_per_side:null,reasons:['FEE_ACCOUNT_MISMATCH_OR_MISSING']},
        scenarios:[{...feeAssessment.scenarios[0],remaining_after_verified_fee_and_scenario_bps_per_side:null}]};
      await page.setContent(renderToStaticMarkup(React.createElement(CostFragilityCell,{row:{
        identity:{trial_id:'fee-candidate'},decimal_rerun:{rerun_hash:'fixture-rerun'},provenance:{},cost_fragility:assessment}})));
      await page.getByText('Proposed slippage sensitivity and evidence limits',{exact:true}).click();
      const text = await page.locator('[data-account-fee-evidence]').textContent();
      assert.match(text,/Fee verification asserts no owner approval/);
      assert.match(text,/Slippage remains a proposed scenario/);
      assert.match(text,/fixture-evidence-identity/);
      assert.match(text,/fixture-verifier/);
      assert.match(text,/fixture-vip/);
      if (verified) {
        assert.match(text,/2\.376543210987654322098765432109876543211/);
        assert.match(text,/-0\.123456789012345677901234567890123456789/);
      } else {
        assert.match(text,/FEE_ACCOUNT_MISMATCH_OR_MISSING/);
        assert.match(text,/After verified taker fee only: unavailable/);
      }
    }
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
