import unittest

from trade_platform.operator_dashboard import (
    _AUTHORIZED_REAL_MARKET_DATA_PROVIDERS,
    _has_synthetic_marker,
    _provenance_flags,
    classify_research_evidence,
    classify_research_evidence_from_markers,
)


class ClassifyResearchEvidenceTests(unittest.TestCase):
    """Module 2B-2.1: fail-closed synthetic-vs-real evidence classification.

    Absence of a synthetic marker must never be treated as proof of real data. Only
    explicit, positively-verified real provenance over a complete lineage may return
    REAL_DATA_RESEARCH_EVIDENCE; everything else falls to UNAVAILABLE.
    """

    # -- Pure boolean-contract matrix -------------------------------------------------

    def test_synthetic_provenance_always_wins(self) -> None:
        for real_verified in (False, True):
            for complete in (False, True):
                self.assertEqual(
                    classify_research_evidence(
                        synthetic_provenance=True,
                        real_data_provenance_verified=real_verified,
                        lineage_complete=complete,
                    ),
                    "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
                )

    def test_real_requires_both_verified_and_complete_lineage(self) -> None:
        self.assertEqual(
            classify_research_evidence(synthetic_provenance=False, real_data_provenance_verified=True, lineage_complete=True),
            "REAL_DATA_RESEARCH_EVIDENCE",
        )

    def test_verified_without_complete_lineage_is_unavailable(self) -> None:
        self.assertEqual(
            classify_research_evidence(synthetic_provenance=False, real_data_provenance_verified=True, lineage_complete=False),
            "UNAVAILABLE",
        )

    def test_complete_lineage_without_verification_is_unavailable(self) -> None:
        self.assertEqual(
            classify_research_evidence(synthetic_provenance=False, real_data_provenance_verified=False, lineage_complete=True),
            "UNAVAILABLE",
        )

    def test_nothing_proven_is_unavailable(self) -> None:
        self.assertEqual(
            classify_research_evidence(synthetic_provenance=False, real_data_provenance_verified=False, lineage_complete=False),
            "UNAVAILABLE",
        )

    # -- _has_synthetic_marker ----------------------------------------------------------

    def test_synthetic_marker_detection(self) -> None:
        self.assertTrue(_has_synthetic_marker("SYNTHETIC_DEMO_ENGINEERING_EVIDENCE"))
        self.assertTrue(_has_synthetic_marker("fixture"))
        self.assertTrue(_has_synthetic_marker(None, "module1b-demo-evidence-v1"))
        self.assertFalse(_has_synthetic_marker("AcmeMarketData", "v-2026-08-19", None))
        self.assertFalse(_has_synthetic_marker())

    # -- _provenance_flags: no real provider is authorized on this platform -----------

    def test_no_authorized_real_providers_configured(self) -> None:
        """Documents current platform reality: this allowlist must stay empty until a
        real market-data provider is actually integrated and authorized."""
        self.assertEqual(_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS, frozenset())

    def test_unknown_provider_never_verifies_as_real(self) -> None:
        # A realistic, marker-free provider name -- the exact gap the old
        # marker-absence heuristic used to fill in as REAL_DATA_RESEARCH_EVIDENCE.
        for provider in ("AcmeMarketData", "Bloomberg", "US:XNYS:REAL-LOOKING-ID", "polygon.io"):
            synthetic, real_verified, complete = _provenance_flags(provider)
            self.assertFalse(synthetic, provider)
            self.assertFalse(real_verified, provider)
            self.assertTrue(complete, provider)
            self.assertEqual(
                classify_research_evidence(
                    synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
                ),
                "UNAVAILABLE",
            )

    def test_no_resolved_provider_is_unavailable_not_synthetic(self) -> None:
        synthetic, real_verified, complete = _provenance_flags(None)
        self.assertEqual((synthetic, real_verified, complete), (False, False, False))
        self.assertEqual(
            classify_research_evidence(
                synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
            ),
            "UNAVAILABLE",
        )

    def test_synthetic_provider_marker_wins_even_with_extra_text(self) -> None:
        synthetic, real_verified, complete = _provenance_flags("fixture", "Independent validation pending")
        self.assertTrue(synthetic)
        self.assertFalse(real_verified)
        self.assertTrue(complete)
        self.assertEqual(
            classify_research_evidence(
                synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
            ),
            "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
        )

    def test_synthetic_texts_alone_can_prove_synthetic_without_a_provider(self) -> None:
        """A validation package/scorecard/contract may declare itself synthetic even when
        no dataset provider row resolves at all -- synthetic detection does not require
        lineage_complete, matching classify_research_evidence()'s own contract."""
        synthetic, _real_verified, complete = _provenance_flags(None, "module1b-demo-evidence-v1")
        self.assertTrue(synthetic)
        self.assertFalse(complete)
        self.assertEqual(
            classify_research_evidence(synthetic_provenance=synthetic, real_data_provenance_verified=False, lineage_complete=complete),
            "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
        )

    def test_provider_on_allowlist_verifies_as_real(self) -> None:
        """Classifier-semantics-only: proves the mechanism resolves REAL once a provider
        is explicitly authorized. Does not claim any provider is authorized today."""
        from unittest.mock import patch

        from trade_platform import operator_dashboard

        with patch.object(operator_dashboard, "_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS", frozenset({"AcmeMarketData"})):
            synthetic, real_verified, complete = operator_dashboard._provenance_flags("AcmeMarketData")
        self.assertFalse(synthetic)
        self.assertTrue(real_verified)
        self.assertTrue(complete)
        self.assertEqual(
            classify_research_evidence(
                synthetic_provenance=synthetic, real_data_provenance_verified=real_verified, lineage_complete=complete,
            ),
            "REAL_DATA_RESEARCH_EVIDENCE",
        )

    # -- classify_research_evidence_from_markers (legacy no-provider-table surfaces) --

    def test_from_markers_never_returns_real(self) -> None:
        for values in (("fixture-v1",), ("US:XNYS:REALISTIC-ID",), ("AcmeMarketData",), ()):
            self.assertIn(
                classify_research_evidence_from_markers(*values),
                {"SYNTHETIC_ENGINEERING_EVIDENCE_ONLY", "UNAVAILABLE"},
            )
            self.assertNotEqual(classify_research_evidence_from_markers(*values), "REAL_DATA_RESEARCH_EVIDENCE")

    def test_from_markers_detects_synthetic_text(self) -> None:
        self.assertEqual(classify_research_evidence_from_markers("module1b-demo-evidence-v1"), "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY")

    def test_from_markers_no_marker_is_unavailable(self) -> None:
        self.assertEqual(classify_research_evidence_from_markers("US:XNYS:REALISTIC-ID"), "UNAVAILABLE")
        self.assertEqual(classify_research_evidence_from_markers(), "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
