from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.providers import StructuredGenerationRequest
from science_buddy.infrastructure.models import Base, Project
from science_buddy.services.brainstorm_plan import (
    BrainstormPlanService,
    normalize_plan_discovery_output,
    preference_readiness,
)
from science_buddy.services.brainstorm_sessions import BrainstormSessionService
from science_buddy.services.brainstorm_types import PreferenceProfile


class PlanRepairModel:
    name = "plan-repair-model"

    def __init__(self) -> None:
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.requests.append(request)
        if len(self.requests) == 1:
            return {"direction_id": "incomplete"}
        return {
            "directions": [
                {
                    "direction_id": f"direction-{index}",
                    "title": f"方向 {index}",
                    "rationale": "可证伪且具备可行性。",
                }
                for index in range(1, 4)
            ],
            "preference_questions": [
                {
                    "question_id": f"question-{index}",
                    "question": f"请选择约束 {index}",
                    "kind": "text",
                }
                for index in range(1, 4)
            ],
        }

    async def stream_text(
        self,
        _system: str,
        _messages: Sequence[str],
    ) -> AsyncIterator[str]:
        yield ""


class UnusablePlanModel(PlanRepairModel):
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.requests.append(request)
        return {"direction_id": "missing-title-and-rationale"}


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


def test_preference_readiness_requires_direction_and_core_constraints() -> None:
    score, missing = preference_readiness(PreferenceProfile(), None)
    assert score == 0
    assert "selected_direction_id" in missing
    complete = PreferenceProfile(
        objective_type="mechanism",
        model_system="organoid",
        budget_level="medium",
        timeline_weeks=24,
        sample_availability="20 samples",
        risk_tolerance="balanced",
    )
    score, missing = preference_readiness(complete, "direction-1")
    assert score == 1
    assert missing == []


def test_partial_preferences_can_build_generation_request() -> None:
    brainstorm = type("PlanSession", (), {})()
    brainstorm.plan_snapshot = {
        "directions": [
            {
                "direction_id": "d1",
                "title": "Direction",
                "evidence_ids": ["ev1.discovery-only"],
            }
        ],
        "selected_direction_id": "d1",
        "preference_profile": PreferenceProfile().model_dump(mode="json"),
        "preference_answers": {},
        "missing_fields": ["objective_type", "model_system", "budget_level"],
    }

    prompt = BrainstormPlanService.build_generation_request(brainstorm)

    assert "unfilled_optional_preferences" in prompt
    assert "objective_type" in prompt
    assert "do not invent user choices" in prompt
    assert "ev1.discovery-only" not in prompt
    assert '"evidence_ids": []' in prompt
    assert "cite only Evidence IDs supplied by the current turn" in prompt


def test_plan_proposal_route_uses_natural_stage_labels() -> None:
    brainstorm = type("PlanSession", (), {})()
    brainstorm.plan_snapshot = {}
    brainstorm.phase = "generating_proposal"

    BrainstormPlanService.mark_proposal_ready(
        brainstorm,
        "flowchart TD\nWP1[WP1 机制筛选] --> M2[M2 功能验证]",
    )

    route = brainstorm.plan_snapshot["technical_route_mermaid"]
    assert brainstorm.phase == "proposal_ready"
    assert "研究阶段1" in route
    assert "方法阶段2" in route
    assert "WP1" not in route
    assert "M2" not in route


def test_plan_discovery_normalizes_single_direction_and_adds_fixed_questions() -> None:
    output, warnings = normalize_plan_discovery_output(
        {
            "direction_id": "coevolution-target",
            "title": "共进化靶点筛选",
            "rationale": "从已有序列关联中筛选可证伪的宿主—噬菌体互作假说。",
            "why_hot": "可连接比较基因组和功能验证",
            "web_sources": ["local-paper.pdf", "https://allowed.example/paper"],
        }
    )

    assert len(output.directions) == 1
    assert output.directions[0].direction_id == "coevolution-target"
    assert output.directions[0].why_hot == ["可连接比较基因组和功能验证"]
    assert output.directions[0].web_sources == ["https://allowed.example/paper"]
    assert [item.question_id for item in output.preference_questions] == [
        "model_system",
        "primary_endpoint",
        "resource_constraints",
    ]
    assert any("单个方向对象" in warning for warning in warnings)
    assert any("不伪造额外方向" in warning for warning in warnings)
    assert any("固定约束问题" in warning for warning in warnings)


def test_plan_discovery_normalizes_root_array_and_alias_fields() -> None:
    output, warnings = normalize_plan_discovery_output(
        {
            "__root_array__": [
                {
                    "name": "方向一",
                    "reason": "理由一",
                    "risks": "样本不足",
                },
                {
                    "topic": "方向二",
                    "description": "理由二",
                    "feasibility": ["已有数据"],
                },
            ]
        }
    )

    assert [item.title for item in output.directions] == ["方向一", "方向二"]
    assert output.directions[0].direction_id.startswith("direction-1-")
    assert output.directions[0].key_risks == ["样本不足"]
    assert output.directions[1].feasibility_notes == ["已有数据"]
    assert len(output.preference_questions) == 3
    assert any("顶层方向数组" in warning for warning in warnings)


def test_plan_discovery_extracts_nested_output_with_scientific_aliases() -> None:
    output, warnings = normalize_plan_discovery_output(
        {
            "output": {
                "direction": {
                    "direction_title": "尾部蛋白适应性进化",
                    "scientific_rationale": "比较受体压力下尾部蛋白变异与宿主范围变化。",
                }
            }
        }
    )

    assert output.directions[0].title == "尾部蛋白适应性进化"
    assert output.directions[0].rationale.startswith("比较受体压力")
    assert any("output" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_plan_discovery_preserves_user_interest_when_both_shapes_are_unusable(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "plan-fallback.db")
    async with sessions() as session:
        project = Project(name="Plan fallback project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Plan fallback session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        model = UnusablePlanModel()
        seed_interest = "鸡尾酒噬菌体疗法与克雷伯菌尾部蛋白共进化"

        result = await BrainstormPlanService(session).discover(
            brainstorm=brainstorm,
            seed_interest=seed_interest,
            model=model,
            evidence=[],
            web_results=[],
        )

    assert len(model.requests) == 2
    assert result.plan.directions[0].title == seed_interest
    assert result.plan.directions[0].evidence_ids == []
    assert len(result.plan.preference_questions) == 3
    warnings = brainstorm.plan_snapshot["normalization_warnings"]
    assert any("原始研究兴趣" in warning for warning in warnings)
    assert brainstorm.phase == "collecting_preferences"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_plan_discovery_repairs_single_direction_shape_once(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "plan-repair.db")
    async with sessions() as session:
        project = Project(name="Plan repair project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Plan repair session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        model = PlanRepairModel()

        result = await BrainstormPlanService(session).discover(
            brainstorm=brainstorm,
            seed_interest="研究共进化机制",
            model=model,
            evidence=[],
            web_results=[],
        )

    assert len(model.requests) == 2
    assert model.requests[1].operation == "brainstorm.plan_discovery.repair"
    assert "at least direction_id, title, and rationale" in model.requests[1].user_content
    assert "Preference questions are optional" in model.requests[1].user_content
    assert len(result.plan.directions) == 3
    assert len(result.plan.preference_questions) == 3
    assert brainstorm.phase == "collecting_preferences"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_plan_preferences_persist_and_enable_generation(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "plan.db")
    async with sessions() as session:
        project = Project(name="Plan project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Plan session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        brainstorm.plan_snapshot = {
            "directions": [{"direction_id": "d1", "title": "Direction"}],
            "missing_fields": [],
        }
        profile = PreferenceProfile(
            objective_type="mechanism",
            model_system="organoid",
            budget_level="medium",
            timeline_weeks=24,
            sample_availability="20 samples",
            risk_tolerance="balanced",
        )
        readiness, missing = await BrainstormPlanService(session).save_preferences(
            brainstorm=brainstorm,
            selected_direction_id="d1",
            profile=profile,
            answers={"endpoint": "viability"},
        )
        prompt = BrainstormPlanService.build_generation_request(brainstorm)

    assert readiness == 1
    assert missing == []
    assert brainstorm.phase == "ready_to_generate"
    assert "organoid" in prompt
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_partial_preferences_still_enable_generation(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "partial-plan.db")
    async with sessions() as session:
        project = Project(name="Partial Plan project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Partial Plan session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        brainstorm.plan_snapshot = {
            "directions": [{"direction_id": "d1", "title": "Direction"}],
        }
        readiness, missing = await BrainstormPlanService(session).save_preferences(
            brainstorm=brainstorm,
            selected_direction_id="d1",
            profile=PreferenceProfile(),
            answers={},
        )
        prompt = BrainstormPlanService.build_generation_request(brainstorm)

    assert readiness < 1
    assert "model_system" in missing
    assert brainstorm.phase == "ready_to_generate"
    assert "unfilled_optional_preferences" in prompt
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_direction_prefetch_service_updates_snapshot(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "prefetch.db")
    async with sessions() as session:
        project = Project(name="Prefetch project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Prefetch session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        brainstorm.plan_snapshot = {
            "directions": [
                {
                    "direction_id": "dir-prefetch-1",
                    "title": "噬菌体基因组囊泡包裹",
                    "rationale": "通过外膜囊泡包裹提升递送效率并规避宿主防御。",
                }
            ]
        }
        mock_settings = type("SettingsMock", (), {
            "literature_request_timeout_seconds": 10,
            "research_literature_target": 8,
        })()
        result = await BrainstormPlanService(session).prefetch_direction_literature(
            brainstorm=brainstorm,
            selected_direction_id="dir-prefetch-1",
            settings=mock_settings,
        )

    assert result["status"] == "succeeded"
    assert result["direction_id"] == "dir-prefetch-1"
    assert brainstorm.plan_snapshot["selected_direction_id"] == "dir-prefetch-1"
    assert brainstorm.plan_snapshot["prefetch_job"]["status"] == "succeeded"
    await engine.dispose()  # type: ignore[attr-defined]
