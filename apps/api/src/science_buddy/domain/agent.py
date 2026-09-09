from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class AgentRole(StrEnum):
    QUERY_PLANNER = "query_planner"
    RETRIEVER = "retriever"
    EVIDENCE_ANALYST = "evidence_analyst"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"


class WorkflowStage(StrEnum):
    INITIAL = "initial"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    VERIFYING = "verifying"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class StepOutcome(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    NEEDS_REFINEMENT = "needs_refinement"
    FATAL_ERROR = "fatal_error"


class AgentMessage(BaseModel):
    workflow_id: UUID
    role: AgentRole


class QueryPlan(BaseModel):
    original_question: str
    normalized_question: str
    pubmed_query: str
    filters: dict[str, str | int | list[str]] = Field(default_factory=dict)


class PlannerMessage(AgentMessage):
    role: AgentRole = AgentRole.QUERY_PLANNER
    plan: QueryPlan


class EvidenceCandidateMessage(BaseModel):
    evidence_id: str
    chunk_id: UUID
    text: str
    source_locator: dict[str, object]


class RetrieverMessage(AgentMessage):
    role: AgentRole = AgentRole.RETRIEVER
    candidates: list[EvidenceCandidateMessage]


class ClaimMessage(BaseModel):
    statement: str
    evidence_ids: list[str]
    relation: str


class AnalystMessage(AgentMessage):
    role: AgentRole = AgentRole.EVIDENCE_ANALYST
    claims: list[ClaimMessage]


class VerificationMessage(AgentMessage):
    role: AgentRole = AgentRole.VERIFIER
    verified_claim_indexes: list[int] = Field(default_factory=list)
    rejected_claim_indexes: list[int] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class SynthesisMessage(AgentMessage):
    role: AgentRole = AgentRole.SYNTHESIZER
    answer: str
    claim_indexes: list[int]


class WorkflowState(BaseModel):
    workflow_id: UUID
    stage: WorkflowStage = WorkflowStage.INITIAL
    refinement_round: int = Field(default=0, ge=0, le=2)
    max_refinement_rounds: int = Field(default=2, ge=0, le=2)
    failure_reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.stage in {WorkflowStage.COMPLETED, WorkflowStage.FAILED}


class InvalidTransitionError(ValueError):
    pass


def transition(state: WorkflowState, outcome: StepOutcome) -> WorkflowState:
    """Apply a deterministic workflow transition with a bounded refinement loop."""
    if state.terminal:
        raise InvalidTransitionError(f"Cannot transition terminal stage {state.stage}")

    if outcome == StepOutcome.FATAL_ERROR:
        return state.model_copy(
            update={"stage": WorkflowStage.FAILED, "failure_reason": "fatal_error"}
        )

    success_path = {
        WorkflowStage.INITIAL: WorkflowStage.PLANNING,
        WorkflowStage.PLANNING: WorkflowStage.RETRIEVING,
        WorkflowStage.RETRIEVING: WorkflowStage.ANALYZING,
        WorkflowStage.ANALYZING: WorkflowStage.VERIFYING,
        WorkflowStage.VERIFYING: WorkflowStage.SYNTHESIZING,
        WorkflowStage.SYNTHESIZING: WorkflowStage.COMPLETED,
    }

    if outcome == StepOutcome.SUCCESS:
        next_stage = success_path.get(state.stage)
        if next_stage is None:
            raise InvalidTransitionError(f"No success transition from {state.stage}")
        return state.model_copy(update={"stage": next_stage})

    can_refine = state.stage in {WorkflowStage.RETRIEVING, WorkflowStage.VERIFYING}
    wants_refinement = outcome in {StepOutcome.NO_RESULTS, StepOutcome.NEEDS_REFINEMENT}
    if can_refine and wants_refinement:
        if state.refinement_round < state.max_refinement_rounds:
            return state.model_copy(
                update={
                    "stage": WorkflowStage.PLANNING,
                    "refinement_round": state.refinement_round + 1,
                }
            )
        return state.model_copy(
            update={"stage": WorkflowStage.FAILED, "failure_reason": "refinement_exhausted"}
        )

    raise InvalidTransitionError(
        f"Outcome {outcome} is invalid while workflow is in stage {state.stage}"
    )
