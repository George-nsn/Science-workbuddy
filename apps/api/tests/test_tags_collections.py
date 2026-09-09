from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import (
    Base,
    CollectionPaper,
    MeSHTerm,
    Paper,
    PaperMeSH,
    PaperTag,
    Project,
    ProjectPaper,
    Tag,
)
from science_buddy.services.collections import RagCollectionService
from science_buddy.services.tags import TagService, normalize_tag_name


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


async def seed_project(session) -> tuple[Project, Paper, Paper]:  # type: ignore[no-untyped-def]
    project = Project(name="Tagged project")
    first = Paper(
        title="BRAF thyroid study",
        authors=[],
        publication_types=["Journal Article"],
    )
    second = Paper(
        title="Control study",
        authors=[],
        publication_types=["Review"],
    )
    session.add_all([project, first, second])
    await session.flush()
    session.add_all(
        [
            ProjectPaper(project_id=project.id, paper_id=first.id),
            ProjectPaper(project_id=project.id, paper_id=second.id),
        ]
    )
    mesh = MeSHTerm(
        descriptor_ui="D013964",
        preferred_label="Thyroid Neoplasms",
        tree_numbers=[],
    )
    session.add(mesh)
    await session.flush()
    session.add(
        PaperMeSH(
            paper_id=first.id,
            descriptor_ui=mesh.descriptor_ui,
            is_major_topic=True,
        )
    )
    await session.commit()
    return project, first, second


def test_normalize_tag_name_collapses_case_and_whitespace() -> None:
    assert normalize_tag_name("  Thyroid   Cancer ") == "thyroid cancer"


@pytest.mark.asyncio
async def test_auto_tags_can_be_manually_replaced_and_reset(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "tags.db")
    async with sessions() as session:
        project, paper, _ = await seed_project(session)
        service = TagService(session)
        automatic = await service.auto_tag_paper(project.id, paper.id)
        assert {value.name for value in automatic} >= {
            "Thyroid Neoplasms",
            "Journal Article",
        }

        manual = await service.replace_manual_tags(
            project.id,
            paper.id,
            ["Priority", "priority", "BRAF"],
        )
        assert {value.name for value in manual} == {"Priority", "BRAF"}
        assert {value.origin for value in manual} == {"manual"}

        await service.auto_tag_paper(project.id, paper.id)
        still_manual = await service.paper_tags(project.id, paper.id)
        assert {value.name for value in still_manual} == {"Priority", "BRAF"}

        reset = await service.reset_to_auto(project.id, paper.id)
        assert {value.name for value in reset} >= {
            "Thyroid Neoplasms",
            "Journal Article",
        }
        assert {value.origin for value in reset} == {"auto"}
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_collection_deduplicates_papers_and_allocates_optional_names(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "collections.db")
    async with sessions() as session:
        project, first, second = await seed_project(session)
        service = RagCollectionService(session)
        collection = await service.create(
            project_id=project.id,
            paper_ids=[first.id, first.id, second.id],
            name="BRAF Evidence",
            build_vector=True,
            build_graph=True,
        )
        duplicate_name = await service.create(
            project_id=project.id,
            paper_ids=[first.id],
            name="BRAF Evidence",
            build_vector=False,
            build_graph=False,
        )
        automatic_name = await service.create(
            project_id=project.id,
            paper_ids=[second.id],
            name=None,
            build_vector=False,
            build_graph=True,
        )
        links = list(
            (
                await session.scalars(
                    select(CollectionPaper).where(
                        CollectionPaper.collection_id == collection.id
                    )
                )
            ).all()
        )

    assert len(links) == 2
    assert duplicate_name.name == "BRAF Evidence (2)"
    assert automatic_name.name.startswith("Knowledge Base ")
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tag_upsert_reuses_normalized_project_tag(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "reuse.db")
    async with sessions() as session:
        project, first, second = await seed_project(session)
        service = TagService(session)
        await service.replace_manual_tags(project.id, first.id, ["Important"])
        await service.replace_manual_tags(project.id, second.id, [" important "])
        tags = list(
            (
                await session.scalars(
                    select(Tag).where(Tag.project_id == project.id)
                )
            ).all()
        )
        links = list(
            (
                await session.scalars(
                    select(PaperTag).where(PaperTag.project_id == project.id)
                )
            ).all()
        )

    assert len([tag for tag in tags if tag.normalized_name == "important"]) == 1
    assert len(links) == 2
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pdf_keyword_tags_survive_auto_tag_refresh(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "pdf-keyword-tags.db")
    async with sessions() as session:
        project, paper, _ = await seed_project(session)
        service = TagService(session)
        await service.auto_tag_paper(project.id, paper.id)
        await service.add_auto_topic_tags(
            project.id,
            paper.id,
            ["Bacteriophages", "Mobile genetic elements"],
        )
        await service.auto_tag_paper(project.id, paper.id)
        tags = await service.paper_tags(project.id, paper.id)

    assert {value.name for value in tags} >= {
        "Bacteriophages",
        "Mobile genetic elements",
        "Thyroid Neoplasms",
    }
    assert {
        value.kind for value in tags if value.name == "Bacteriophages"
    } == {"pdf_keyword"}
    await engine.dispose()  # type: ignore[attr-defined]
