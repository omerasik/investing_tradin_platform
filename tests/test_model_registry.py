import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.model_registry import (
    ModelDriftObservation,
    ModelPrediction,
    ModelStatus,
    ModelValidation,
    ModelVersion,
    SQLiteModelRegistry,
)


def model() -> ModelVersion:
    return ModelVersion.create(
        name="return-direction", version="v1", task="return_direction_probability",
        feature_version="features:v3", dataset_version="bars:v7", artifact_reference="local://models/return-direction-v1",
    )


class ModelRegistryTests(unittest.TestCase):
    def test_approval_requires_matching_validation_and_is_fail_closed_before_then_approved(self) -> None:
        registry = SQLiteModelRegistry()
        candidate = model()
        registry.register(candidate)
        self.assertFalse(registry.is_approved(candidate.model_id))
        validation = ModelValidation.create(
            model_id=candidate.model_id, metrics={"brier_score": Decimal("0.12"), "roc_auc": Decimal("0.71")},
            economic_value_after_cost=Decimal("0.03"), evidence_reference="research://validation/123",
        )
        registry.append_validation(validation)
        registry.approve(candidate.model_id, validation_id=validation.validation_id, actor="model-reviewer", reason="held-out review complete")
        self.assertTrue(registry.is_approved(candidate.model_id))
        registry.close()

    def test_drift_disables_approved_model_and_reapproval_is_explicit(self) -> None:
        registry = SQLiteModelRegistry(); candidate = model(); registry.register(candidate)
        validation = ModelValidation.create(model_id=candidate.model_id, metrics={"brier_score": Decimal("0.12")}, economic_value_after_cost=Decimal("0.03"), evidence_reference="research://validation/123")
        registry.append_validation(validation)
        registry.approve(candidate.model_id, validation_id=validation.validation_id, actor="reviewer", reason="approved")
        self.assertTrue(registry.record_drift(ModelDriftObservation.create(model_id=candidate.model_id, metric="feature_psi", score=Decimal("0.31"), threshold=Decimal("0.2"))))
        self.assertEqual(registry.status(candidate.model_id), ModelStatus.DISABLED)
        with self.assertRaises(PermissionError):
            registry.approve(candidate.model_id, validation_id=validation.validation_id, actor="reviewer", reason="not explicit")
        registry.approve(candidate.model_id, validation_id=validation.validation_id, actor="reviewer", reason="reviewed after drift", explicit_reapproval=True)
        self.assertTrue(registry.is_approved(candidate.model_id))
        registry.close()

    def test_registry_evidence_survives_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.sqlite"
            first = SQLiteModelRegistry(path); candidate = model(); first.register(candidate)
            validation = ModelValidation.create(model_id=candidate.model_id, metrics={"log_loss": Decimal("0.4")}, economic_value_after_cost=Decimal("0.01"), evidence_reference="research://validation/456")
            first.append_validation(validation)
            first.approve(candidate.model_id, validation_id=validation.validation_id, actor="reviewer", reason="approved")
            first.close()
            reopened = SQLiteModelRegistry(path)
            self.assertTrue(reopened.is_approved(candidate.model_id))
            self.assertEqual(len(reopened.events(candidate.model_id)), 1)
            reopened.close()

    def test_prediction_is_versioned_explainable_and_expiring(self) -> None:
        registry = SQLiteModelRegistry(); candidate = model(); registry.register(candidate)
        validation = ModelValidation.create(model_id=candidate.model_id, metrics={"brier_score": Decimal("0.12")}, economic_value_after_cost=Decimal("0.03"), evidence_reference="research://validation/789")
        registry.append_validation(validation)
        registry.approve(candidate.model_id, validation_id=validation.validation_id, actor="reviewer", reason="approved")
        issued = validation.validated_at
        prediction = ModelPrediction.create(model_id=candidate.model_id, instrument_id="US:NYSE:SPY", horizon="1d", prediction=Decimal("0.012"), confidence=Decimal("0.7"), calibration=Decimal("0.1"), feature_version="features:v3", data_version="bars:v9", regime="BULL", explanation="positive trend features", uncertainty=Decimal("0.2"), issued_at=issued, expires_at=issued + timedelta(days=1))
        registry.append_prediction(prediction)
        self.assertEqual(registry.active_prediction(candidate.model_id, "US:NYSE:SPY", "1d", as_of=issued).prediction_id, prediction.prediction_id)
        with self.assertRaises(KeyError):
            registry.active_prediction(candidate.model_id, "US:NYSE:SPY", "1d", as_of=issued + timedelta(days=1))
        registry.close()


if __name__ == "__main__":
    unittest.main()
