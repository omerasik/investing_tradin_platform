import React from "react";

export const TERMINAL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
export type TerminalSymbol = typeof TERMINAL_SYMBOLS[number];
export function terminalSymbol(value: unknown): TerminalSymbol | undefined {
  return TERMINAL_SYMBOLS.find((symbol) => symbol === value);
}
/** Only the read model's exact catalogue association can establish a study's symbol. */
export function matchesSymbol(study: { instrument: string | null; instrument_source: string }, symbol: TerminalSymbol): boolean {
  return study.instrument_source === "RESEARCH_WINDOW_CATALOGUE" && study.instrument === symbol;
}
export function SymbolNavigation({ symbol, catalogue, unavailable }: {
  symbol?: TerminalSymbol; catalogue?: boolean; unavailable?: string;
}) {
  return <section aria-label="Market to research selection">
    <h3>Choose a market for research</h3>
    <nav aria-label="Terminal markets">
      <a href="/terminal#market-scan" aria-current={!symbol ? "page" : undefined}>All markets</a>
      {TERMINAL_SYMBOLS.map((item) => <React.Fragment key={item}>{" / "}<a
        href={`/terminal?symbol=${item}#strategy-lab`} aria-current={symbol === item ? "page" : undefined}>{item}</a></React.Fragment>)}
    </nav>
    <p>{symbol ? `Research selection: ${symbol}.` : "No market filter selected."} Changing market starts a new comparison selection.</p>
    {unavailable ? <p className="empty-notice">{unavailable}</p> : catalogue === false
      ? <p className="empty-notice">Symbol association unavailable: no research window catalogue is configured. Study labels are never used to guess a market.</p> : null}
  </section>;
}
