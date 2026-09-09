from dataclasses import dataclass
from typing import Literal

ClaimLabel = Literal["entailment", "contradiction", "insufficient"]


@dataclass(frozen=True, slots=True)
class ClaimCalibrationExample:
    confidence: float
    predicted_label: ClaimLabel
    human_label: ClaimLabel


@dataclass(frozen=True, slots=True)
class ClaimCalibrationResult:
    threshold: float
    precision: float
    recall: float
    f1: float
    examples: int
    version: str = "claim-threshold-calibration-v1"


def calibrate_entailment_threshold(
    examples: list[ClaimCalibrationExample],
    *,
    candidates: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85, 0.90),
) -> ClaimCalibrationResult:
    """Select the highest-F1 entailment threshold from a human-labeled set."""
    if not examples:
        raise ValueError("At least one human-labeled example is required")
    if any(not 0 <= value.confidence <= 1 for value in examples):
        raise ValueError("Confidence values must be between 0 and 1")
    best: ClaimCalibrationResult | None = None
    for threshold in candidates:
        true_positive = false_positive = false_negative = 0
        for value in examples:
            predicts_entailment = (
                value.predicted_label == "entailment"
                and value.confidence >= threshold
            )
            is_entailment = value.human_label == "entailment"
            true_positive += int(predicts_entailment and is_entailment)
            false_positive += int(predicts_entailment and not is_entailment)
            false_negative += int(not predicts_entailment and is_entailment)
        precision = true_positive / max(1, true_positive + false_positive)
        recall = true_positive / max(1, true_positive + false_negative)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        result = ClaimCalibrationResult(
            threshold=threshold,
            precision=precision,
            recall=recall,
            f1=f1,
            examples=len(examples),
        )
        if best is None or (result.f1, result.threshold) > (best.f1, best.threshold):
            best = result
    if best is None:
        raise RuntimeError("No calibration threshold was evaluated")
    return best
