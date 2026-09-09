from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import Base, Chunk, DocumentAsset, Paper, Section
from science_buddy.services.structured_evidence import (
	StructuredEvidenceDraft,
	StructuredEvidenceService,
)


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
	engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
	sessions = async_sessionmaker(engine, expire_on_commit=False)
	async with engine.begin() as connection:
		await connection.run_sync(Base.metadata.create_all)
	return engine, sessions


@pytest.mark.asyncio
async def test_table_cells_require_locator_and_parent_for_evidence_eligibility(
	tmp_path: Path,
) -> None:
	engine, sessions = await make_database(tmp_path / "structured-evidence.db")
	async with sessions() as session:
		paper = Paper(title="Table paper", authors=[], publication_types=[])
		session.add(paper)
		await session.flush()
		asset = DocumentAsset(
			paper_id=paper.id,
			source=AssetSource.USER_PDF,
				evidence_depth=EvidenceDepth.USER_PDF,
			parse_status=ParseStatus.READY,
			content_hash="a" * 64,
		)
		session.add(asset)
		await session.flush()
		section = Section(
			asset_id=asset.id,
			section_path="Results",
			title="Results",
			ordinal=0,
		)
		session.add(section)
		await session.flush()
		chunk = Chunk(
			section_id=section.id,
			ordinal=0,
			text="Table 1 reports response rates.",
			content_hash="b" * 64,
			char_start=0,
			char_end=31,
			source_locator={},
		)
		session.add(chunk)
		await session.flush()
		service = StructuredEvidenceService(session)
		table = (
			await service.register(
				asset_id=asset.id,
				drafts=[
					StructuredEvidenceDraft(
						object_type="table",
						text_content="Table 1 response rates",
						source_key="table-1",
						page_number=3,
						bounding_box={"x0": 10, "y0": 20, "x1": 300, "y1": 500},
						parent_chunk_id=chunk.id,
					)
				],
			)
		)[0]
		cells = await service.register(
			asset_id=asset.id,
			drafts=[
				StructuredEvidenceDraft(
					object_type="table_cell",
					text_content="42%",
					source_key="table-1-B2",
					page_number=3,
					bounding_box={"x0": 100, "y0": 100, "x1": 130, "y1": 120},
					cell_range="B2",
					parent_chunk_id=chunk.id,
					parent_object_id=table.id,
				),
				StructuredEvidenceDraft(
					object_type="figure",
					text_content="Kaplan-Meier plot",
					source_key="figure-1",
					page_number=4,
					bounding_box={"x0": 10, "y0": 20, "x1": 300, "y1": 500},
					parent_chunk_id=chunk.id,
				),
			],
		)

	assert table.evidence_eligible
	assert cells[0].evidence_eligible
	assert not cells[1].evidence_eligible
	await engine.dispose()  # type: ignore[attr-defined]
