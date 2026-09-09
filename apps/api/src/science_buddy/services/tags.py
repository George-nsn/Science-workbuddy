import re
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    DocumentAsset,
    MeSHTerm,
    Paper,
    PaperMeSH,
    PaperTag,
    Project,
    ProjectPaper,
    Tag,
)

_WHITESPACE = re.compile(r"\s+")


def normalize_tag_name(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


@dataclass(frozen=True, slots=True)
class TagValue:
    id: UUID
    name: str
    kind: str
    origin: str


class TagService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_project_auto_tags(self, project_id: UUID) -> None:
        links = list(
            (
                await self._session.scalars(
                    select(ProjectPaper).where(
                        ProjectPaper.project_id == project_id,
                        ProjectPaper.tags_manually_curated.is_(False),
                    )
                )
            ).all()
        )
        for link in links:
            has_tag = await self._session.scalar(
                select(PaperTag.tag_id)
                .where(
                    PaperTag.project_id == project_id,
                    PaperTag.paper_id == link.paper_id,
                )
                .limit(1)
            )
            if has_tag is None:
                await self.auto_tag_paper(project_id, link.paper_id)

    async def auto_tag_paper(self, project_id: UUID, paper_id: UUID) -> list[TagValue]:
        link = await self._session.get(ProjectPaper, (project_id, paper_id))
        paper = await self._session.get(Paper, paper_id)
        if link is None or paper is None:
            raise ValueError("Paper is not part of the selected project")
        if link.tags_manually_curated:
            return await self.paper_tags(project_id, paper_id)

        mesh_rows = (
            await self._session.execute(
                select(PaperMeSH, MeSHTerm)
                .join(MeSHTerm, MeSHTerm.descriptor_ui == PaperMeSH.descriptor_ui)
                .where(PaperMeSH.paper_id == paper_id)
                .order_by(PaperMeSH.is_major_topic.desc(), MeSHTerm.preferred_label)
                .limit(8)
            )
        ).all()
        desired: list[tuple[str, str]] = [
            (term.preferred_label, "mesh") for _mapping, term in mesh_rows
        ]
        desired.extend((value, "publication_type") for value in paper.publication_types[:4])
        assets = list(
            (
                await self._session.scalars(
                    select(DocumentAsset).where(DocumentAsset.paper_id == paper_id)
                )
            ).all()
        )
        if any(asset.source.value == "user_pdf" for asset in assets):
            desired.append(("User PDF", "source"))
        if any(asset.evidence_depth.value == "fulltext_xml" for asset in assets):
            desired.append(("Open Full Text", "evidence_depth"))
        elif any(asset.evidence_depth.value == "abstract" for asset in assets):
            desired.append(("Abstract", "evidence_depth"))
        await self._session.execute(
            delete(PaperTag).where(
                PaperTag.project_id == project_id,
                PaperTag.paper_id == paper_id,
                PaperTag.origin == "auto",
                PaperTag.tag_id.in_(
                    select(Tag.id).where(
                        Tag.project_id == project_id,
                        Tag.kind != "pdf_keyword",
                    )
                ),
            )
        )
        for name, kind in self._unique_labels(desired):
            tag = await self._get_or_create(project_id, name, kind)
            self._session.add(
                PaperTag(
                    project_id=project_id,
                    paper_id=paper_id,
                    tag_id=tag.id,
                    origin="auto",
                )
            )
        await self._session.flush()
        return await self.paper_tags(project_id, paper_id)

    async def replace_manual_tags(
        self, project_id: UUID, paper_id: UUID, labels: list[str]
    ) -> list[TagValue]:
        link = await self._session.get(ProjectPaper, (project_id, paper_id))
        if link is None:
            raise ValueError("Paper is not part of the selected project")
        normalized = self._unique_labels((label, "manual") for label in labels)
        if len(normalized) > 20:
            raise ValueError("A paper can have at most 20 tags")
        await self._session.execute(
            delete(PaperTag).where(
                PaperTag.project_id == project_id,
                PaperTag.paper_id == paper_id,
            )
        )
        for name, kind in normalized:
            tag = await self._get_or_create(project_id, name, kind)
            self._session.add(
                PaperTag(
                    project_id=project_id,
                    paper_id=paper_id,
                    tag_id=tag.id,
                    origin="manual",
                )
            )
        link.tags_manually_curated = True
        project = await self._session.get(Project, project_id)
        if project is not None:
            project.retrieval_revision += 1
        await self._session.commit()
        return await self.paper_tags(project_id, paper_id)

    async def reset_to_auto(self, project_id: UUID, paper_id: UUID) -> list[TagValue]:
        link = await self._session.get(ProjectPaper, (project_id, paper_id))
        if link is None:
            raise ValueError("Paper is not part of the selected project")
        link.tags_manually_curated = False
        await self._session.execute(
            delete(PaperTag).where(
                PaperTag.project_id == project_id,
                PaperTag.paper_id == paper_id,
            )
        )
        await self._session.flush()
        values = await self.auto_tag_paper(project_id, paper_id)
        project = await self._session.get(Project, project_id)
        if project is not None:
            project.retrieval_revision += 1
        await self._session.commit()
        return values

    async def add_session_tags(
        self,
        project_id: UUID,
        paper_id: UUID,
        labels: list[str],
    ) -> list[TagValue]:
        link = await self._session.get(ProjectPaper, (project_id, paper_id))
        if link is None:
            raise ValueError("Paper is not part of the selected project")
        for name, _kind in self._unique_labels((label, "brainstorm") for label in labels):
            tag = await self._get_or_create(project_id, name, "brainstorm")
            await self._session.execute(
                sqlite_insert(PaperTag)
                .values(
                    project_id=project_id,
                    paper_id=paper_id,
                    tag_id=tag.id,
                    origin="brainstorm",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        PaperTag.project_id,
                        PaperTag.paper_id,
                        PaperTag.tag_id,
                    ]
                )
            )
        project = await self._session.get(Project, project_id)
        if project is not None:
            project.retrieval_revision += 1
        await self._session.flush()
        return await self.paper_tags(project_id, paper_id)

    async def add_auto_topic_tags(
        self,
        project_id: UUID,
        paper_id: UUID,
        labels: list[str],
    ) -> list[TagValue]:
        link = await self._session.get(ProjectPaper, (project_id, paper_id))
        if link is None:
            raise ValueError("Paper is not part of the selected project")
        if link.tags_manually_curated:
            return await self.paper_tags(project_id, paper_id)
        for name, _kind in self._unique_labels((label, "pdf_keyword") for label in labels):
            tag = await self._get_or_create(project_id, name, "pdf_keyword")
            await self._session.execute(
                sqlite_insert(PaperTag)
                .values(
                    project_id=project_id,
                    paper_id=paper_id,
                    tag_id=tag.id,
                    origin="auto",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        PaperTag.project_id,
                        PaperTag.paper_id,
                        PaperTag.tag_id,
                    ]
                )
            )
        await self._session.flush()
        return await self.paper_tags(project_id, paper_id)

    async def paper_tags(self, project_id: UUID, paper_id: UUID) -> list[TagValue]:
        rows = (
            await self._session.execute(
                select(Tag, PaperTag.origin)
                .join(PaperTag, PaperTag.tag_id == Tag.id)
                .where(
                    PaperTag.project_id == project_id,
                    PaperTag.paper_id == paper_id,
                )
                .order_by(Tag.name)
            )
        ).all()
        return [TagValue(tag.id, tag.name, tag.kind, origin) for tag, origin in rows]

    async def project_tag_counts(self, project_id: UUID) -> list[tuple[Tag, int]]:
        rows = (
            await self._session.execute(
                select(Tag, func.count(PaperTag.paper_id))
                .outerjoin(PaperTag, PaperTag.tag_id == Tag.id)
                .where(Tag.project_id == project_id)
                .group_by(Tag.id)
                .having(func.count(PaperTag.paper_id) > 0)
                .order_by(func.count(PaperTag.paper_id).desc(), Tag.name)
            )
        ).all()
        return [(tag, int(count)) for tag, count in rows]

    async def _get_or_create(self, project_id: UUID, name: str, kind: str) -> Tag:
        normalized = normalize_tag_name(name)
        if not normalized or len(name) > 80:
            raise ValueError("Tag names must contain 1-80 characters")
        await self._session.execute(
            sqlite_insert(Tag)
            .values(
                id=uuid4(),
                project_id=project_id,
                name=name.strip(),
                normalized_name=normalized,
                kind=kind,
            )
            .on_conflict_do_nothing(
                index_elements=[Tag.project_id, Tag.normalized_name]
            )
        )
        tag = await self._session.scalar(
            select(Tag).where(
                Tag.project_id == project_id,
                Tag.normalized_name == normalized,
            )
        )
        if tag is None:
            raise RuntimeError("Tag upsert did not return a stored tag")
        return tag

    @staticmethod
    def _unique_labels(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for name, kind in values:
            normalized = normalize_tag_name(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append((name.strip(), kind))
        return result
