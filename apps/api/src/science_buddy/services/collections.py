from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    CollectionPaper,
    Project,
    ProjectPaper,
    RagCollection,
)


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    collection: RagCollection
    paper_count: int


class RagCollectionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: UUID,
        paper_ids: list[UUID],
        name: str | None,
        build_vector: bool,
        build_graph: bool,
    ) -> RagCollection:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError("Project does not exist")
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
        missing = set(selected) - project_papers
        if missing:
            raise ValueError("Every selected paper must belong to the project")
        collection = await self._create_with_unique_name(
            project_id=project_id,
            requested=name,
            build_vector=build_vector,
            build_graph=build_graph,
        )
        self._session.add_all(
            CollectionPaper(collection_id=collection.id, paper_id=paper_id)
            for paper_id in selected
        )
        project.retrieval_revision += 1
        await self._session.commit()
        return collection

    async def paper_ids(self, collection_id: UUID) -> set[UUID]:
        return set(
            (
                await self._session.scalars(
                    select(CollectionPaper.paper_id).where(
                        CollectionPaper.collection_id == collection_id
                    )
                )
            ).all()
        )

    async def summaries(self, project_id: UUID) -> list[CollectionSummary]:
        rows = (
            await self._session.execute(
                select(RagCollection, func.count(CollectionPaper.paper_id))
                .outerjoin(
                    CollectionPaper,
                    CollectionPaper.collection_id == RagCollection.id,
                )
                .where(RagCollection.project_id == project_id)
                .group_by(RagCollection.id)
                .order_by(RagCollection.created_at.desc())
            )
        ).all()
        return [CollectionSummary(collection, int(count)) for collection, count in rows]

    async def _unique_name(self, project_id: UUID, requested: str | None) -> str:
        base = " ".join((requested or "").split())[:200]
        if not base:
            base = f"Knowledge Base {datetime.now().strftime('%Y-%m-%d %H%M')}"
        existing = set(
            (
                await self._session.scalars(
                    select(RagCollection.name).where(RagCollection.project_id == project_id)
                )
            ).all()
        )
        if base not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})" in existing:
            suffix += 1
        suffix_text = f" ({suffix})"
        return f"{base[: 200 - len(suffix_text)]}{suffix_text}"

    async def _create_with_unique_name(
        self,
        *,
        project_id: UUID,
        requested: str | None,
        build_vector: bool,
        build_graph: bool,
    ) -> RagCollection:
        candidate = await self._unique_name(project_id, requested)
        for _attempt in range(20):
            try:
                async with self._session.begin_nested():
                    collection = RagCollection(
                        project_id=project_id,
                        name=candidate,
                        vector_status=("pending" if build_vector else "not_requested"),
                        graph_status=("pending" if build_graph else "not_requested"),
                    )
                    self._session.add(collection)
                    await self._session.flush()
                return collection
            except IntegrityError:
                candidate = await self._unique_name(project_id, candidate)
        raise ValueError("Could not allocate a unique collection name")
