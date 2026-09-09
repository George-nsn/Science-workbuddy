from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import literature as literature_api
from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import (
    ArchivePaper,
    Base,
    CollectionPaper,
    DocumentAsset,
    LiteratureArchive,
    Paper,
    PaperTag,
    Project,
    ProjectPaper,
    RagCollection,
    Tag,
)
from science_buddy.main import app
from science_buddy.services.library_management import (
    LiteratureArchiveService,
    ProjectPaperDeletionService,
)


class RecordingCache:
    def __init__(self) -> None:
        self.namespaces: list[str] = []

    async def invalidate_namespace(self, namespace: str) -> None:
        self.namespaces.append(namespace)


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_named_archive_reuses_normalized_name_and_appends_papers(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "archives.db")
    async with sessions() as session:
        project = Project(name="Archive project")
        first = Paper(title="First", authors=[], publication_types=[])
        second = Paper(title="Second", authors=[], publication_types=[])
        session.add_all([project, first, second])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=project.id, paper_id=first.id),
                ProjectPaper(project_id=project.id, paper_id=second.id),
            ]
        )
        await session.commit()
        service = LiteratureArchiveService(session)
        initial = await service.add_papers(
            project_id=project.id,
            paper_ids=[first.id],
            name="  Phage   defense ",
        )
        appended = await service.add_papers(
            project_id=project.id,
            paper_ids=[first.id, second.id],
            name="phage defense",
        )
        summaries = await service.summaries(project.id)
        archive_count = await session.scalar(select(func.count()).select_from(LiteratureArchive))
        link_count = await session.scalar(select(func.count()).select_from(ArchivePaper))

    assert initial.archive.id == appended.archive.id
    assert initial.archive.origin == "manual"
    assert appended.paper_count == 2
    assert len(summaries) == 1
    assert summaries[0].paper_count == 2
    assert archive_count == 1
    assert link_count == 2
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_project_delete_cleans_links_orphan_data_and_empty_collection(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "deletion.db")
    orphan_pdf = tmp_path / "orphan.pdf"
    orphan_pdf.write_bytes(b"pdf")
    async with sessions() as session:
        project = Project(name="Delete project")
        first = Paper(title="Delete me", authors=[], publication_types=[])
        second = Paper(title="Keep me", authors=[], publication_types=[])
        session.add_all([project, first, second])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=project.id, paper_id=first.id),
                ProjectPaper(project_id=project.id, paper_id=second.id),
            ]
        )
        tag = Tag(
            project_id=project.id,
            name="Selected",
            normalized_name="selected",
            kind="manual",
        )
        session.add(tag)
        archive = LiteratureArchive(
            project_id=project.id,
            name="Archive",
            normalized_name="archive",
        )
        session.add(archive)
        collection = RagCollection(
            project_id=project.id,
            name="Mixed collection",
            vector_status="ready",
            graph_status="not_requested",
        )
        empty_collection = RagCollection(
            project_id=project.id,
            name="Will become empty",
            vector_status="ready",
            graph_status="not_requested",
        )
        session.add_all([collection, empty_collection])
        await session.flush()
        session.add_all(
            [
                PaperTag(
                    project_id=project.id,
                    paper_id=first.id,
                    tag_id=tag.id,
                    origin="manual",
                ),
                ArchivePaper(archive_id=archive.id, paper_id=first.id),
                CollectionPaper(collection_id=collection.id, paper_id=first.id),
                CollectionPaper(collection_id=collection.id, paper_id=second.id),
                CollectionPaper(collection_id=empty_collection.id, paper_id=first.id),
                DocumentAsset(
                    paper_id=first.id,
                    source=AssetSource.USER_PDF,
                    evidence_depth=EvidenceDepth.USER_PDF,
                    parse_status=ParseStatus.READY,
                    content_hash="delete-content",
                    storage_path=str(orphan_pdf),
                ),
            ]
        )
        await session.commit()
        first_id = first.id
        result = await ProjectPaperDeletionService(session).delete(
            project_id=project.id,
            paper_ids=[first.id],
        )
        remaining_collection_papers = set(
            (
                await session.scalars(
                    select(CollectionPaper.paper_id).where(
                        CollectionPaper.collection_id == collection.id
                    )
                )
            ).all()
        )
        stored_first = await session.get(Paper, first_id)

    assert result.removed_from_project == 1
    assert result.deleted_globally == 1
    assert result.collections_updated == 1
    assert result.collections_deleted == 1
    assert stored_first is None
    assert remaining_collection_papers == {second.id}
    assert not orphan_pdf.exists()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_project_delete_preserves_paper_shared_with_another_project(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "shared-paper.db")
    async with sessions() as session:
        first_project = Project(name="First project")
        second_project = Project(name="Second project")
        paper = Paper(title="Shared paper", authors=[], publication_types=[])
        session.add_all([first_project, second_project, paper])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=first_project.id, paper_id=paper.id),
                ProjectPaper(project_id=second_project.id, paper_id=paper.id),
            ]
        )
        await session.commit()
        paper_id = paper.id
        result = await ProjectPaperDeletionService(session).delete(
            project_id=first_project.id,
            paper_ids=[paper.id],
        )
        remaining_link = await session.get(ProjectPaper, (second_project.id, paper.id))
        stored_paper = await session.get(Paper, paper_id)

    assert result.removed_from_project == 1
    assert result.deleted_globally == 0
    assert remaining_link is not None
    assert stored_paper is not None
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_archive_filter_and_bulk_delete_api(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "library-api.db")
    async with sessions() as session:
        project = Project(name="API archive project")
        first = Paper(title="Archived paper", authors=[], publication_types=[])
        second = Paper(title="Visible paper", authors=[], publication_types=[])
        session.add_all([project, first, second])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=project.id, paper_id=first.id),
                ProjectPaper(project_id=project.id, paper_id=second.id),
            ]
        )
        await session.commit()
        project_id = project.id
        first_id = first.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    cache = RecordingCache()
    app.dependency_overrides[literature_api.get_session] = override_session
    app.state.cache = cache
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            archived = await client.post(
                "/api/v1/library/archives",
                json={
                    "project_id": str(project_id),
                    "paper_ids": [str(first_id)],
                    "name": "Named archive",
                },
            )
            archive_id = archived.json()["id"]
            filtered = await client.get(
                "/api/v1/library",
                params={"project_id": str(project_id), "archive_id": archive_id},
            )
            deleted = await client.post(
                "/api/v1/library/papers/bulk-delete",
                json={
                    "project_id": str(project_id),
                    "paper_ids": [str(first_id)],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert archived.status_code == 200
    assert archived.json()["paper_count"] == 1
    assert filtered.status_code == 200
    assert [paper["title"] for paper in filtered.json()["papers"]] == [
        "Archived paper"
    ]
    assert deleted.status_code == 200
    assert deleted.json()["removed_from_project"] == 1
    assert cache.namespaces == [f"retrieval:{project_id}"]
    await engine.dispose()  # type: ignore[attr-defined]
