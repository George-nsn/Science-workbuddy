from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import settings as settings_api
from science_buddy.config import Settings
from science_buddy.infrastructure.models import (
    Base,
    ModelConfiguration,
    WebSearchConfiguration,
)
from science_buddy.main import app
from science_buddy.services.models import resolve_model_settings
from science_buddy.services.web_search import resolve_web_search_settings


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_local_model_configuration_is_encrypted_and_never_returned(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "model-settings.db")
    settings = Settings(
        _env_file=None,
        evidence_signing_key=SecretStr("test-encryption-key-that-is-long-enough"),
    )

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.get("/api/v1/settings/model")
            saved = await client.put(
                "/api/v1/settings/model",
                json={
                    "provider": "openai_compatible",
                    "model": "gpt-test",
                    "base_url": "https://models.example.test/v1/",
                    "api_key": "top-secret-api-key",
                },
            )
            loaded = await client.get("/api/v1/settings/model")
            updated = await client.put(
                "/api/v1/settings/model",
                json={
                    "provider": "openai_compatible",
                    "model": "gpt-test-2",
                    "base_url": "https://models.example.test/v1",
                    "api_key": None,
                },
            )
            endpoint_without_key = await client.put(
                "/api/v1/settings/model",
                json={
                    "provider": "anthropic",
                    "model": "claude-test",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key": None,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert empty.status_code == 200
    assert empty.json()["source"] == "none"
    assert saved.status_code == 200
    assert saved.json() == {
        "configured": True,
        "provider": "openai_compatible",
        "model": "gpt-test",
        "base_url": "https://models.example.test/v1",
        "has_api_key": True,
        "source": "local",
    }
    assert "top-secret-api-key" not in saved.text
    assert loaded.json() == saved.json()
    assert "api_key" not in loaded.json()
    assert updated.status_code == 200
    assert updated.json()["model"] == "gpt-test-2"
    assert endpoint_without_key.status_code == 422
    assert "API Key again" in endpoint_without_key.json()["detail"]

    async with sessions() as session:
        stored = await session.get(ModelConfiguration, 1)
        assert stored is not None
        assert stored.api_key_encrypted != "top-secret-api-key"
        assert "top-secret-api-key" not in stored.api_key_encrypted
        resolved = await resolve_model_settings(session, settings)
        assert resolved.llm_model == "gpt-test-2"
        assert resolved.llm_api_key is not None
        assert resolved.llm_api_key.get_secret_value() == "top-secret-api-key"

    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            deleted = await client.delete("/api/v1/settings/model")
            after_delete = await client.get("/api/v1/settings/model")
    finally:
        app.dependency_overrides.clear()

    assert deleted.status_code == 204
    assert after_delete.json()["configured"] is False
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_environment_model_configuration_cannot_be_overridden(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "environment-settings.db")
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        llm_model="claude-test",
        llm_api_key=SecretStr("environment-secret"),
    )

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            loaded = await client.get("/api/v1/settings/model")
            replaced = await client.put(
                "/api/v1/settings/model",
                json={
                    "provider": "openai_compatible",
                    "model": "gpt-test",
                    "api_key": "replacement-secret",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert loaded.status_code == 200
    assert loaded.json()["source"] == "environment"
    assert loaded.json()["provider"] == "anthropic"
    assert "environment-secret" not in loaded.text
    assert replaced.status_code == 409
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_connection_uses_candidate_without_persisting_key(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "connection-test.db")
    settings = Settings(_env_file=None)
    observed_provider_names: list[str] = []

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    async def fake_probe(provider) -> None:  # type: ignore[no-untyped-def]
        observed_provider_names.append(provider.name)

    monkeypatch.setattr(settings_api, "probe_model_provider", fake_probe)
    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/settings/model/test",
                json={
                    "provider": "anthropic",
                    "model": "claude-test",
                    "base_url": "https://anthropic.example.test/v1",
                    "api_key": "connection-only-secret",
                },
            )
            insecure = await client.post(
                "/api/v1/settings/model/test",
                json={
                    "provider": "openai_compatible",
                    "model": "test-model",
                    "base_url": "http://models.example.test/v1",
                    "api_key": "connection-only-secret",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert observed_provider_names == ["anthropic"]
    assert "connection-only-secret" not in response.text
    assert insecure.status_code == 422
    assert "only allowed on localhost" in insecure.json()["detail"]
    async with sessions() as session:
        assert await session.get(ModelConfiguration, 1) is None
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_catalog_and_local_provider_without_key(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "local-model-settings.db")
    settings = Settings(_env_file=None)

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog_response = await client.get("/api/v1/settings/model/catalog")
            saved = await client.put(
                "/api/v1/settings/model",
                json={
                    "provider": "ollama",
                    "model": "qwen3:8b",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key": None,
                },
            )
            status_response = await client.get("/api/v1/research/model-status")
    finally:
        app.dependency_overrides.clear()

    assert catalog_response.status_code == 200
    providers = {
        value["id"]: value
        for value in catalog_response.json()["providers"]
    }
    assert {
        "openai",
        "anthropic",
        "deepseek",
        "google_gemini",
        "qwen",
        "kimi",
        "zhipu",
        "siliconflow",
        "openrouter",
        "github_copilot",
        "ollama",
        "lmstudio",
        "openai_compatible",
    } <= providers.keys()
    assert providers["deepseek"]["default_base_url"] == "https://api.deepseek.com"
    assert {model["id"] for model in providers["deepseek"]["models"]} == {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }
    assert providers["ollama"]["api_key_required"] is False
    assert providers["github_copilot"]["protocol"] == "github_copilot"
    assert providers["github_copilot"]["api_key_required"] is False
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["has_api_key"] is False
    assert status_response.json() == {
        "configured": True,
        "provider": "ollama",
        "model": "qwen3:8b",
    }
    async with sessions() as session:
        stored = await session.get(ModelConfiguration, 1)
        assert stored is not None
        assert stored.api_key_encrypted is None
        resolved = await resolve_model_settings(session, settings)
        assert resolved.llm_provider == "ollama"
        assert resolved.llm_api_key is None
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tavily_configuration_is_encrypted_and_not_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await make_database(tmp_path / "tavily-settings.db")
    settings = Settings(
        _env_file=None,
        evidence_signing_key=SecretStr("tavily-encryption-key-long-enough"),
    )

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    async def fake_probe(_settings) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(settings_api, "probe_web_search", fake_probe)
    app.dependency_overrides[settings_api.get_session] = override_session
    app.dependency_overrides[settings_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            saved = await client.put(
                "/api/v1/settings/web-search",
                json={
                    "provider": "tavily",
                    "base_url": "https://api.tavily.com/",
                    "api_key": "tavily-secret-key",
                    "max_results": 6,
                    "search_depth": "advanced",
                },
            )
            loaded = await client.get("/api/v1/settings/web-search")
            tested = await client.post(
                "/api/v1/settings/web-search/test",
                json={
                    "provider": "tavily",
                    "base_url": "https://api.tavily.com",
                    "api_key": None,
                    "max_results": 6,
                    "search_depth": "advanced",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert saved.status_code == 200
    assert loaded.json() == saved.json()
    assert "tavily-secret-key" not in loaded.text
    assert "api_key" not in loaded.json()
    assert tested.status_code == 200
    async with sessions() as session:
        stored = await session.get(WebSearchConfiguration, 1)
        assert stored is not None
        assert stored.api_key_encrypted != "tavily-secret-key"
        resolved = await resolve_web_search_settings(session, settings)
        assert resolved is not None
        assert resolved.api_key == "tavily-secret-key"
    await engine.dispose()  # type: ignore[attr-defined]
