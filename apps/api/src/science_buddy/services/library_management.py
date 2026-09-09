from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    ArchivePaper,
    BrainstormLiterature,
    CollectionPaper,
    DocumentAsset,
    GraphCommunity,
    GraphEdge,
    GraphEntityMention,
    GraphNode,
    GraphPathAudit,
    LiteratureArchive,
    Paper,
    PaperTag,
    Project,
    ProjectPaper,
    RagCollection,
)
from science_buddy.services.knowledge_graph import KnowledgeGraphBuilder


def normalize_archive_name(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class ArchiveSummary:
    archive: LiteratureArchive
    paper_count: int


@dataclass(frozen=True, slots=True)
class ProjectDeletionResult:
    requested: int
    removed_from_project: int
    deleted_globally: int
    collections_updated: int
    collections_deleted: int


class LiteratureArchiveService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_papers(
        self,
        *,
        project_id: UUID,
        paper_ids: list[UUID],
        name: str,
        origin: str = "manual",
        reuse_name: bool = True,
    ) -> ArchiveSummary:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError("Project does not exist")
        cleaned_name = " ".join(name.split())
        if not cleaned_name or len(cleaned_name) > 200:
            raise ValueError("Archive name must contain 1-200 characters")
        if origin not in {"manual", "upload_batch"}:
            raise ValueError("Unsupported archive origin")
        selected = list(dict.fromkeys(paper_ids))
        project_papers = set(
            (
                await self._session.scalars(
                    select(ProjectPaper.paper_id).where(
                        ProjectPaper.project_id == project_id,
                        ProjectPaper.paper_id.in_(selected),
                    )
                )
            ).all()
        )
        if set(selected) != project_papers:
            raise ValueError("Every selected paper must belong to the project")
        normalized = normalize_archive_name(cleaned_name)
        archive = None
        if reuse_name:
            archive = await self._session.scalar(
                select(LiteratureArchive).where(
                    LiteratureArchive.project_id == project_id,
                    LiteratureArchive.normalized_name == normalized,
                )
            )
        if archive is None:
            if not reuse_name:
                normalized = f"{normalized}-{uuid4().hex[:12]}"
            archive = LiteratureArchive(
                project_id=project_id,
                name=cleaned_name,
                normalized_name=normalized,
                origin=origin,
            )
            self._session.add(archive)
            await self._session.flush()
        for paper_id in selected:
            await self._session.execute(
                sqlite_insert(ArchivePaper)
                .values(archive_id=archive.id, paper_id=paper_id)
                .on_conflict_do_nothing(
                    index_elements=[ArchivePaper.archive_id, ArchivePaper.paper_id]
                )
            )
        await self._session.commit()
        paper_count = int(
            (
                await self._session.scalar(
                    select(func.count(ArchivePaper.paper_id)).where(
                        ArchivePaper.archive_id == archive.id
                    )
                )
            )
            or 0
        )
        return ArchiveSummary(archive=archive, paper_count=paper_count)

    async def summaries(self, project_id: UUID) -> list[ArchiveSummary]:
        rows = (
            await self._session.execute(
                select(LiteratureArchive, func.count(ArchivePaper.paper_id))
                .outerjoin(
                    ArchivePaper,
                    ArchivePaper.archive_id == LiteratureArchive.id,
                )
                .where(LiteratureArchive.project_id == project_id)
                .group_by(LiteratureArchive.id)
                .order_by(LiteratureArchive.updated_at.desc(), LiteratureArchive.name)
            )
        ).all()
        return [ArchiveSummary(archive, int(count)) for archive, count in rows]


class ProjectPaperDeletionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def delete(
        self,
        *,
        project_id: UUID,
        paper_ids: list[UUID],
    ) -> ProjectDeletionResult:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError("Project does not exist")
        requested = list(dict.fromkeys(paper_ids))
        selected = set(
            (
                await self._session.scalars(
                    select(ProjectPaper.paper_id).where(
                        ProjectPaper.project_id == project_id,
                        ProjectPaper.paper_id.in_(requested),
                    )
                )
            ).all()
        )
        if not selected:
            raise ValueError("None of the selected papers belongs to the project")

        collection_ids = set(
            (
                await self._session.scalars(
                    select(RagCollection.id)
                    .join(
                        CollectionPaper,
                        CollectionPaper.collection_id == RagCollection.id,
                    )
                    .where(
                        RagCollection.project_id == project_id,
                        CollectionPaper.paper_id.in_(selected),
                    )
                )
            ).all()
        )
        archive_ids = set(
            (
                await self._session.scalars(
                    select(LiteratureArchive.id).where(
                        LiteratureArchive.project_id == project_id
                    )
                )
            ).all()
        )
        await self._session.execute(
            delete(PaperTag).where(
                PaperTag.project_id == project_id,
                PaperTag.paper_id.in_(selected),
            )
        )
        if archive_ids:
            await self._session.execute(
                delete(ArchivePaper).where(
                    ArchivePaper.archive_id.in_(archive_ids),
                    ArchivePaper.paper_id.in_(selected),
                )
            )
        if collection_ids:
            await self._session.execute(
                delete(CollectionPaper).where(
                    CollectionPaper.collection_id.in_(collection_ids),
                    CollectionPaper.paper_id.in_(selected),
                )
            )
        await self._session.execute(
            delete(ProjectPaper).where(
                ProjectPaper.project_id == project_id,
                ProjectPaper.paper_id.in_(selected),
            )
        )
        await self._session.flush()

        empty_collections: set[UUID] = set()
        remaining_collections: set[UUID] = set()
        for collection_id in collection_ids:
            count = int(
                (
                    await self._session.scalar(
                        select(func.count(CollectionPaper.paper_id)).where(
                            CollectionPaper.collection_id == collection_id
                        )
                    )
                )
                or 0
            )
            if count:
                remaining_collections.add(collection_id)
            else:
                empty_collections.add(collection_id)
        if empty_collections:
            for collection_id in empty_collections:
                await self._clear_graph_scope(
                    project_id,
                    f"collection:{collection_id}",
                    commit=False,
                )
            await self._session.execute(
                delete(RagCollection).where(RagCollection.id.in_(empty_collections))
            )

        deleted_globally = 0
        storage_paths: list[Path] = []
        # Aggregate reference counts in four grouped queries instead of 4*N
        # per-paper COUNT scans.
        reference_counts: dict[UUID, int] = defaultdict(int)
        for model, field in (
            (ProjectPaper, ProjectPaper.paper_id),
            (CollectionPaper, CollectionPaper.paper_id),
            (ArchivePaper, ArchivePaper.paper_id),
            (BrainstormLiterature, BrainstormLiterature.paper_id),
        ):
            grouped = await self._session.execute(
                select(field, func.count())
                .select_from(model)
                .where(field.in_(selected))
                .group_by(field)
            )
            for paper_id, count in grouped:
                reference_counts[paper_id] += int(count)
        for paper_id in selected:
            if reference_counts.get(paper_id, 0):
                continue
            storage_paths.extend(
                Path(value)
                for value in (
                    await self._session.scalars(
                        select(DocumentAsset.storage_path).where(
                            DocumentAsset.paper_id == paper_id,
                            DocumentAsset.storage_path.is_not(None),
                        )
                    )
                ).all()
                if value
            )
            paper = await self._session.get(Paper, paper_id)
            if paper is not None:
                await self._session.delete(paper)
                deleted_globally += 1

        project.retrieval_revision += 1
        await self._session.commit()
        await self._refresh_graphs(
            project_id=project_id,
            collection_ids=remaining_collections,
        )
        for path in storage_paths:
            path.unlink(missing_ok=True)
        return ProjectDeletionResult(
            requested=len(requested),
            removed_from_project=len(selected),
            deleted_globally=deleted_globally,
            collections_updated=len(remaining_collections),
            collections_deleted=len(empty_collections),
        )

    async def _refresh_graphs(
        self,
        *,
        project_id: UUID,
        collection_ids: set[UUID],
    ) -> None:
        remaining_project_papers = set(
            (
                await self._session.scalars(
                    select(ProjectPaper.paper_id).where(
                        ProjectPaper.project_id == project_id
                    )
                )
            ).all()
        )
        if remaining_project_papers:
            await KnowledgeGraphBuilder(self._session).rebuild(project_id)
        else:
            await self._clear_graph_scope(project_id, "project")
        for collection_id in collection_ids:
            collection = await self._session.get(RagCollection, collection_id)
            if collection is None or collection.graph_status == "not_requested":
                continue
            paper_ids = set(
                (
                    await self._session.scalars(
                        select(CollectionPaper.paper_id).where(
                            CollectionPaper.collection_id == collection_id
                        )
                    )
                ).all()
            )
            try:
                await KnowledgeGraphBuilder(self._session).rebuild(
                    project_id,
                    paper_ids=paper_ids,
                    scope_key=f"collection:{collection_id}",
                )
                collection = await self._session.get(RagCollection, collection_id)
                if collection is not None:
                    collection.graph_status = "ready"
                    await self._session.commit()
            except ValueError:
                collection = await self._session.get(RagCollection, collection_id)
                if collection is not None:
                    collection.graph_status = "failed"
                    await self._session.commit()

    async def _clear_graph_scope(
        self,
        project_id: UUID,
        scope_key: str,
        *,
        commit: bool = True,
    ) -> None:
        await self._session.execute(
            delete(GraphPathAudit).where(
                GraphPathAudit.project_id == project_id,
                GraphPathAudit.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphEntityMention).where(
                GraphEntityMention.project_id == project_id,
                GraphEntityMention.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphCommunity).where(
                GraphCommunity.project_id == project_id,
                GraphCommunity.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphEdge).where(
                GraphEdge.project_id == project_id,
                GraphEdge.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphNode).where(
                GraphNode.project_id == project_id,
                GraphNode.scope_key == scope_key,
            )
        )
        if commit:
            await self._session.commit()
