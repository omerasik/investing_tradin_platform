# Model Governance

Models, features, data, prompts and configurations are versioned and approved separately. Predictions record horizon, confidence, calibration, explanation, uncertainty and expiry. Monitor data, feature, prediction, calibration, performance, regime and execution drift; drift may reduce or disable risk but never increase it.
## Regime and ensemble controls

Regime inference is a transparent, historical-prefix-only probability model, not a claim of market certainty. Every estimate records probabilities for bullish, bearish and sideways states plus derived uncertainty. Ensemble weights are based on documented regime-weighted expected scores and are rejected when they breach the configured maximum; they are never silently capped. Any replacement with a learned classifier requires versioning, chronological validation, calibration evidence, drift monitoring and independent approval.
