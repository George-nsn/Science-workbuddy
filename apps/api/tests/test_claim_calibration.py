import pytest

from science_buddy.services.claim_calibration import (
    ClaimCalibrationExample,
    calibrate_entailment_threshold,
)


def test_claim_threshold_calibration_uses_human_labels() -> None:
    result = calibrate_entailment_threshold(
        [
            ClaimCalibrationExample(0.95, "entailment", "entailment"),
            ClaimCalibrationExample(0.80, "entailment", "entailment"),
            ClaimCalibrationExample(0.70, "entailment", "insufficient"),
            ClaimCalibrationExample(0.90, "contradiction", "contradiction"),
        ]
    )

    assert result.threshold >= 0.75
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.version == "claim-threshold-calibration-v1"


def test_claim_threshold_calibration_requires_examples() -> None:
    with pytest.raises(ValueError, match="human-labeled"):
        calibrate_entailment_threshold([])
