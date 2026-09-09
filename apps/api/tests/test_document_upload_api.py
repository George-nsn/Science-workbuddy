from pathlib import Path
from uuid import UUID

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import documents as documents_api
from science_buddy.config import Settings, get_settings
from science_buddy.infrastructure.models import (
    ArchivePaper,
    Base,
    LiteratureArchive,
    Paper,
    PaperTag,
    Project,
    Tag,
)
from science_buddy.main import app


def text_pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), (text + " ") * 12)
    payload = document.tobytes()
    document.close()
    return payload


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_single_pdf_upload_uses_filename_stem_as_title(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "single-upload.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")
    async with sessions() as session:
        project = Project(name="PDF upload project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    pdf_payload = text_pdf_bytes("Traceable single PDF evidence.")
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload",
                data={"project_id": str(project_id)},
                files={
                    "file": (
                        "single-paper.pdf",
                        pdf_payload,
                        "application/pdf",
                    )
                },
            )
            duplicate = await client.post(
                "/api/v1/documents/upload",
                data={"project_id": str(project_id)},
                files={
                    "file": (
                        "renamed-copy.pdf",
                        pdf_payload,
                        "application/pdf",
                    )
                },
            )
            enrichment = await client.post(
                "/api/v1/documents/enrich-pdfs",
                json={"project_id": str(project_id)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["paper_id"] == response.json()["paper_id"]
    assert enrichment.status_code == 200
    assert enrichment.json()["processed"] == 1
    assert enrichment.json()["summaries_updated"] == 1
    async with sessions() as session:
        paper = await session.scalar(
            select(Paper).where(Paper.id == UUID(response.json()["paper_id"]))
        )
        tags = (
            await session.execute(
                select(Tag.name, Tag.kind)
                .join(PaperTag, PaperTag.tag_id == Tag.id)
                .where(PaperTag.paper_id == UUID(response.json()["paper_id"]))
            )
        ).all()
        assert paper is not None
        assert paper.title == "single-paper"
        assert paper.abstract is not None
        assert paper.abstract_source == "local_extractive_pdf"
        assert ("User PDF", "source") in tags
        count = await session.scalar(select(func.count()).select_from(Paper))
        assert count == 1
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_folder_upload_reports_each_pdf_and_isolates_failures(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "folder-upload.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")
    async with sessions() as session:
        project = Project(name="Folder upload project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload-batch",
                files=[
                    ("project_id", (None, str(project_id))),
                    ("relative_paths", (None, "topic/first.pdf")),
                    ("relative_paths", (None, "topic/broken.pdf")),
                    ("relative_paths", (None, "topic/nested/second.pdf")),
                    (
                        "files",
                        (
                            "first.pdf",
                            text_pdf_bytes("First folder PDF evidence."),
                            "application/pdf",
                        ),
                    ),
                    ("files", ("broken.pdf", b"not-a-pdf", "application/pdf")),
                    (
                        "files",
                        (
                            "second.pdf",
                            text_pdf_bytes("Second folder PDF evidence."),
                            "application/pdf",
                        ),
                    ),
                ],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert payload["imported"] == 2
    assert payload["failed"] == 1
    assert len(payload["paper_ids"]) == 2
    assert payload["archive"]["origin"] == "upload_batch"
    assert payload["archive"]["paper_count"] == 2
    assert payload["archive"]["name"].startswith("新增档案 · ")
    assert [item["relative_path"] for item in payload["items"]] == [
        "topic/first.pdf",
        "topic/broken.pdf",
        "topic/nested/second.pdf",
    ]
    failed = next(item for item in payload["items"] if item["status"] == "failed")
    assert failed["filename"] == "broken.pdf"
    assert "Only PDF files" in failed["error"]
    async with sessions() as session:
        titles = set((await session.scalars(select(Paper.title))).all())
        archive = await session.scalar(select(LiteratureArchive))
        archive_links = await session.scalar(select(func.count()).select_from(ArchivePaper))
    assert titles == {"first", "second"}
    assert archive is not None
    assert archive.origin == "upload_batch"
    assert archive_links == 2
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_all_failed_batch_does_not_create_archive(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "failed-batch.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload-batch",
                files=[("files", ("broken.pdf", b"not-pdf", "application/pdf"))],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["archive"] is None
    assert response.json()["paper_ids"] == []
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(LiteratureArchive))
    assert count == 0
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_server_side_failure_does_not_abort_remaining_batch_files(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "batch-server-error.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    async def failing_ingestion(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated server failure")

    monkeypatch.setattr(
        documents_api.DocumentIngestionService,
        "ingest_sections",
        failing_ingestion,
    )
    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload-batch",
                files=[
                    ("files", ("one.pdf", text_pdf_bytes("First PDF."), "application/pdf")),
                    ("files", ("two.pdf", text_pdf_bytes("Second PDF."), "application/pdf")),
                ],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["imported"] == 0
    assert payload["failed"] == 2
    assert all(item["status"] == "failed" for item in payload["items"])
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_folder_upload_enforces_file_count_limit(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "folder-limit.db")
    settings = Settings(
        _env_file=None,
        upload_directory=tmp_path / "uploads",
        max_batch_upload_files=1,
    )

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload-batch",
                files=[
                    ("files", ("one.pdf", b"%PDF-one", "application/pdf")),
                    ("files", ("two.pdf", b"%PDF-two", "application/pdf")),
                ],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 413
    assert "at most 1 PDF" in response.json()["detail"]
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unexpected_upload_failure_returns_cors_json(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "upload-error.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    async def fail_ingestion(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(
        documents_api.DocumentIngestionService,
        "ingest_sections",
        fail_ingestion,
    )
    app.dependency_overrides[documents_api.get_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents/upload",
                headers={"Origin": "http://127.0.0.1:3000"},
                files={
                    "file": (
                        "failure.pdf",
                        text_pdf_bytes("Failure response PDF."),
                        "application/pdf",
                    )
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "PDF import failed unexpectedly; check the API log and retry"
    )
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    await engine.dispose()  # type: ignore[attr-defined]
