from uuid import uuid4

import pytest

from science_buddy.domain.agent import (
    InvalidTransitionError,
    StepOutcome,
    WorkflowStage,
    WorkflowState,
    transition,
)


def make_state() -> WorkflowState:
    return WorkflowState(workflow_id=uuid4())


def test_success_path_is_deterministic() -> None:
    state = make_state()
    expected = [
        WorkflowStage.PLANNING,
        WorkflowStage.RETRIEVING,
        WorkflowStage.ANALYZING,
        WorkflowStage.VERIFYING,
        WorkflowStage.SYNTHESIZING,
        WorkflowStage.COMPLETED,
    ]

    for stage in expected:
        state = transition(state, StepOutcome.SUCCESS)
        assert state.stage == stage

    assert state.terminal


def test_refinement_is_bounded_to_two_rounds() -> None:
    state = WorkflowState(workflow_id=uuid4(), stage=WorkflowStage.VERIFYING)

    state = transition(state, StepOutcome.NEEDS_REFINEMENT)
    assert state.stage == WorkflowStage.PLANNING
    assert state.refinement_round == 1

    state = state.model_copy(update={"stage": WorkflowStage.VERIFYING})
    state = transition(state, StepOutcome.NEEDS_REFINEMENT)
    assert state.stage == WorkflowStage.PLANNING
    assert state.refinement_round == 2

    state = state.model_copy(update={"stage": WorkflowStage.VERIFYING})
    state = transition(state, StepOutcome.NEEDS_REFINEMENT)
    assert state.stage == WorkflowStage.FAILED
    assert state.failure_reason == "refinement_exhausted"


def test_terminal_state_rejects_more_transitions() -> None:
    state = WorkflowState(workflow_id=uuid4(), stage=WorkflowStage.COMPLETED)

    with pytest.raises(InvalidTransitionError):
        transition(state, StepOutcome.SUCCESS)
