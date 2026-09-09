import hashlib
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    Chunk,
    DocumentAsset,
    StructuredEvidenceObject,
)

ObjectType = Literal["figure", "table", "equation", "caption", "table_cell"]


@dataclass(frozen=True, slots=True)
class StructuredEvidenceDraft:
    object_type: ObjectType
    text_content: str
    source_key: str
    label: str | None = None
    page_number: int | None = None
    bounding_box: dict[str, float] | None = None
    cell_range: str | None = None
    source_locator: dict[str, Any] | None = None
    extraction_metadata: dict[str, Any] | None = None
    parent_chunk_id: UUID | None = None
    parent_object_id: UUID | None = None


class StructuredEvidenceService:
    """Persist parser-derived objects; evidence eligibility is mechanical and narrow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        *,
        asset_id: UUID,
        drafts: list[StructuredEvidenceDraft],
    ) -> list[StructuredEvidenceObject]:
        asset = await self._session.get(DocumentAsset, asset_id)
        if asset is None:
            raise ValueError("Document asset does not exist")
        output: list[StructuredEvidenceObject] = []
        for draft in drafts:
            text = " ".join(draft.text_content.split())[:50000]
            if not text:
                continue
            if draft.parent_chunk_id is not None:
                chunk = await self._session.get(Chunk, draft.parent_chunk_id)
                if chunk is None:
                    raise ValueError("Structured evidence parent chunk does not exist")
            bbox = draft.bounding_box or {}
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            existing = await self._session.scalar(
                select(StructuredEvidenceObject).where(
                    StructuredEvidenceObject.asset_id == asset_id,
                    StructuredEvidenceObject.object_type == draft.object_type,
                    StructuredEvidenceObject.content_hash == content_hash,
                    StructuredEvidenceObject.source_key == draft.source_key,
                )
            )
            eligible = self._evidence_eligible(draft, text)
            if existing is None:
                existing = StructuredEvidenceObject(
                    asset_id=asset_id,
                    parent_chunk_id=draft.parent_chunk_id,
                    parent_object_id=draft.parent_object_id,
                    object_type=draft.object_type,
                    label=draft.label,
                    text_content=text,
                    page_number=draft.page_number,
                    bounding_box=bbox,
                    cell_range=draft.cell_range,
                    source_key=draft.source_key,
                    source_locator=draft.source_locator or {},
                    extraction_metadata=draft.extraction_metadata or {},
                    content_hash=content_hash,
                    evidence_eligible=eligible,
                )
                self._session.add(existing)
            else:
                existing.evidence_eligible = eligible
                existing.source_locator = draft.source_locator or existing.source_locator
                existing.extraction_metadata = (
                    draft.extraction_metadata or existing.extraction_metadata
                )
            output.append(existing)
        await self._session.flush()
        return output

    @staticmethod
    def _evidence_eligible(draft: StructuredEvidenceDraft, text: str) -> bool:
        has_locator = draft.page_number is not None and bool(draft.bounding_box)
        if draft.object_type == "table_cell":
            return bool(draft.cell_range and draft.parent_object_id and has_locator and text)
        if draft.object_type in {"table", "equation"}:
            return bool(draft.parent_chunk_id and has_locator and text)
        # Figures and captions remain retrieval context until a type-specific verifier exists.
        return False
