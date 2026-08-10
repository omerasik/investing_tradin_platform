import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { env } from "node:process";

export type DashboardConfig = {
  apiBaseUrl?: string;
  operatorTokenEnv?: string;
  operatorTokenFile?: string;
  returnProvider?: string;
  returnAccount?: string;
  investmentThesisId?: string;
  investmentPortfolioId?: string;
  paperOrderIntentId?: string;
  paperAccountId?: string;
  strategyId?: string;
  experimentId?: string;
  promotionDecisionId?: string;
  dashboardOrigin: string;
};

function optionalText(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !value.trim()) throw new Error(`invalid_dashboard_config_${field}`);
  return value.trim();
}

function validateUrl(value: string | undefined, field: string): string | undefined {
  if (!value) return undefined;
  const url = new URL(value);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) {
    throw new Error(`invalid_dashboard_config_${field}`);
  }
  return url.toString().replace(/\/$/, "");
}

function validateEnvReference(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (!/^[A-Z][A-Z0-9_]*$/.test(value)) throw new Error("invalid_dashboard_config_operator_token_env");
  return value;
}

function validateSecretFile(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (!isAbsolute(value)) throw new Error("invalid_dashboard_config_operator_token_file");
  return value;
}

/**
 * Load deployment-owned, non-secret configuration at request time.
 * `dashboard.config.json` is deliberately ignored by source control; the example
 * manifest documents the contract. Environment settings remain a compatibility
 * fallback for existing deployments.
 */
export function loadDashboardConfig(): DashboardConfig {
  const path = env.TRADE_PLATFORM_DASHBOARD_CONFIG_PATH ?? join(process.cwd(), "dashboard.config.json");
  let raw: Record<string, unknown> = {};
  if (existsSync(path)) {
    try {
      const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid_dashboard_config_document");
      raw = parsed as Record<string, unknown>;
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("invalid_dashboard_config")) throw error;
      throw new Error("invalid_dashboard_config_document");
    }
  }
  const operatorTokenFile = validateSecretFile(optionalText(raw.operator_token_file, "operator_token_file"));
  const operatorTokenEnv = validateEnvReference(optionalText(raw.operator_token_env ?? (operatorTokenFile ? undefined : "TRADE_PLATFORM_OPERATOR_TOKEN"), "operator_token_env"));
  if (operatorTokenFile && operatorTokenEnv) throw new Error("invalid_dashboard_config_operator_token_reference");
  return {
    apiBaseUrl: validateUrl(optionalText(raw.api_base_url ?? env.TRADE_PLATFORM_API_BASE_URL, "api_base_url"), "api_base_url"),
    operatorTokenEnv,
    operatorTokenFile,
    returnProvider: optionalText(raw.return_provider ?? env.TRADE_PLATFORM_RETURN_PROVIDER, "return_provider"),
    returnAccount: optionalText(raw.return_account ?? env.TRADE_PLATFORM_RETURN_ACCOUNT, "return_account"),
    investmentThesisId: optionalText(raw.investment_thesis_id ?? env.TRADE_PLATFORM_INVESTMENT_THESIS_ID, "investment_thesis_id"),
    investmentPortfolioId: optionalText(raw.investment_portfolio_id ?? env.TRADE_PLATFORM_INVESTMENT_PORTFOLIO_ID, "investment_portfolio_id"),
    paperOrderIntentId: optionalText(raw.paper_order_intent_id ?? env.TRADE_PLATFORM_PAPER_ORDER_INTENT_ID, "paper_order_intent_id"),
    paperAccountId: optionalText(raw.paper_account_id ?? env.TRADE_PLATFORM_PAPER_ACCOUNT_ID, "paper_account_id"),
    strategyId: optionalText(raw.strategy_id ?? env.TRADE_PLATFORM_STRATEGY_ID, "strategy_id"),
    experimentId: optionalText(raw.experiment_id ?? env.TRADE_PLATFORM_EXPERIMENT_ID, "experiment_id"),
    promotionDecisionId: optionalText(raw.promotion_decision_id ?? env.TRADE_PLATFORM_PROMOTION_DECISION_ID, "promotion_decision_id"),
    dashboardOrigin: validateUrl(optionalText(raw.dashboard_origin ?? env.TRADE_PLATFORM_DASHBOARD_ORIGIN ?? "http://127.0.0.1:3000", "dashboard_origin"), "dashboard_origin")!,
  };
}

export function resolveDashboardOperatorToken(config: DashboardConfig): string | undefined {
  if (config.operatorTokenEnv) return env[config.operatorTokenEnv];
  try { return config.operatorTokenFile ? readFileSync(config.operatorTokenFile, "utf8").trim() || undefined : undefined; }
  catch { return undefined; }
}
