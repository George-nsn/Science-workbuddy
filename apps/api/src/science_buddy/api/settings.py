from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    ModelCatalogOptionResponse,
    ModelCatalogProviderResponse,
    ModelCatalogResponse,
    ModelConfigurationRequest,
    ModelConfigurationResponse,
    ModelConnectionTestResponse,
    WebSearchConfigurationRequest,
    WebSearchConfigurationResponse,
    WebSearchConnectionTestResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import ModelConfiguration, WebSearchConfiguration
from science_buddy.services.models import (
    MODEL_PROVIDER_CATALOG,
    ModelConfigurationError,
    ModelResponseError,
    build_model_provider,
    encrypt_model_api_key,
    model_provider_requires_api_key,
    model_settings_configured,
    normalize_model_provider,
    probe_model_provider,
    resolve_model_settings,
    validate_model_base_url,
)
from science_buddy.services.web_search import (
    WebSearchConfigurationError,
    WebSearchError,
    WebSearchSettings,
    encrypt_web_search_key,
    probe_web_search,
    resolve_web_search_settings,
    validate_web_search_base_url,
    web_search_environment_configured,
)

router = APIRouter(prefix="/settings", tags=["settings"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _environment_configured(settings: Settings) -> bool:
    return model_settings_configured(settings)


@router.get("/model/catalog", response_model=ModelCatalogResponse)
async def get_model_catalog() -> ModelCatalogResponse:
    return ModelCatalogResponse(
        providers=[
            ModelCatalogProviderResponse(
                id=spec.id,
                label=spec.label,
                protocol=spec.protocol,
                description=spec.description,
                default_base_url=spec.default_base_url,
                api_key_required=spec.api_key_required,
                models=[
                    ModelCatalogOptionResponse(
                        id=model.id,
                        label=model.label,
                        category=model.category,
                    )
                    for model in spec.models
                ],
            )
            for spec in MODEL_PROVIDER_CATALOG.values()
        ]
    )


@router.get("/model", response_model=ModelConfigurationResponse)
async def get_model_configuration(
    session: SessionDependency,
    settings: SettingsDependency,
) -> ModelConfigurationResponse:
    if _environment_configured(settings):
        provider = normalize_model_provider(settings.llm_provider or "")
        return ModelConfigurationResponse(
            configured=True,
            provider=provider,
            model=settings.llm_model,
            base_url=validate_model_base_url(provider, settings.llm_base_url),
            has_api_key=bool(settings.llm_api_key),
            source="environment",
        )
    stored = await session.get(ModelConfiguration, 1)
    if stored is None:
        return ModelConfigurationResponse(
            configured=False,
            provider=None,
            model=None,
            base_url=None,
            has_api_key=False,
            source="none",
        )
    return ModelConfigurationResponse(
        configured=True,
        provider=stored.provider,
        model=stored.model,
        base_url=stored.base_url,
        has_api_key=bool(stored.api_key_encrypted),
        source="local",
    )


@router.put("/model", response_model=ModelConfigurationResponse)
async def save_model_configuration(
    payload: ModelConfigurationRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> ModelConfigurationResponse:
    if _environment_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Environment model configuration is active and cannot be replaced in the UI",
        )
    try:
        provider = normalize_model_provider(payload.provider)
        base_url = validate_model_base_url(provider, payload.base_url)
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = await session.get(ModelConfiguration, 1)
    requires_key = model_provider_requires_api_key(provider)
    if stored is None:
        if requires_key and not payload.api_key:
            raise HTTPException(status_code=422, detail="API Key is required")
        stored = ModelConfiguration(
            id=1,
            provider=provider,
            model=payload.model,
            base_url=base_url,
            api_key_encrypted=(
                encrypt_model_api_key(payload.api_key.get_secret_value(), settings)
                if payload.api_key
                else None
            ),
        )
        session.add(stored)
    else:
        endpoint_changed = stored.provider != provider or stored.base_url != base_url
        if requires_key and not payload.api_key and (
            endpoint_changed or not stored.api_key_encrypted
        ):
            raise HTTPException(
                status_code=422,
                detail="Enter the API Key again when changing provider or Base URL",
            )
        stored.provider = provider
        stored.model = payload.model
        stored.base_url = base_url
        if payload.api_key:
            stored.api_key_encrypted = encrypt_model_api_key(
                payload.api_key.get_secret_value(), settings
            )
        elif endpoint_changed and not requires_key:
            stored.api_key_encrypted = None
    await session.commit()
    return ModelConfigurationResponse(
        configured=True,
        provider=stored.provider,
        model=stored.model,
        base_url=stored.base_url,
        has_api_key=bool(stored.api_key_encrypted),
        source="local",
    )


@router.post("/model/test", response_model=ModelConnectionTestResponse)
async def test_model_configuration(
    payload: ModelConfigurationRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> ModelConnectionTestResponse:
    try:
        provider_name = normalize_model_provider(payload.provider)
        requires_key = model_provider_requires_api_key(provider_name)
        base_url = validate_model_base_url(provider_name, payload.base_url)
        key = payload.api_key.get_secret_value() if payload.api_key else None
        if not key and requires_key:
            resolved = await resolve_model_settings(session, settings)
            resolved_provider = (
                normalize_model_provider(resolved.llm_provider)
                if resolved.llm_provider
                else None
            )
            resolved_base_url = (
                validate_model_base_url(resolved_provider, resolved.llm_base_url)
                if resolved_provider
                else None
            )
            if resolved_provider != provider_name or resolved_base_url != base_url:
                raise ModelConfigurationError(
                    "Enter the API Key again when changing provider or Base URL"
                )
            key = (
                resolved.llm_api_key.get_secret_value() if resolved.llm_api_key else None
            )
        if not key and requires_key:
            raise ModelConfigurationError("Enter an API Key before testing the connection")
        candidate = settings.model_copy(
            update={
                "llm_provider": provider_name,
                "llm_model": payload.model,
                "llm_base_url": base_url,
                "llm_api_key": SecretStr(key) if key else None,
            }
        )
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            await probe_model_provider(build_model_provider(client, candidate))
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ModelConnectionTestResponse(
        provider=provider_name,
        model=payload.model,
        detail="Model connection succeeded",
    )


@router.delete("/model", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_configuration(
    session: SessionDependency,
    settings: SettingsDependency,
) -> Response:
    if _environment_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Environment model configuration cannot be removed in the UI",
        )
    stored = await session.get(ModelConfiguration, 1)
    if stored is not None:
        await session.delete(stored)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/web-search", response_model=WebSearchConfigurationResponse)
async def get_web_search_configuration(
    session: SessionDependency,
    settings: SettingsDependency,
) -> WebSearchConfigurationResponse:
    resolved = await resolve_web_search_settings(session, settings)
    if resolved is None:
        return WebSearchConfigurationResponse(
            configured=False,
            provider=None,
            base_url=None,
            has_api_key=False,
            max_results=settings.web_search_max_results,
            search_depth="advanced",
            source="none",
        )
    return WebSearchConfigurationResponse(
        configured=True,
        provider="tavily",
        base_url=resolved.base_url,
        has_api_key=bool(resolved.api_key),
        max_results=resolved.max_results,
        search_depth=resolved.search_depth,
        source=resolved.source,
    )


@router.put("/web-search", response_model=WebSearchConfigurationResponse)
async def save_web_search_configuration(
    payload: WebSearchConfigurationRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> WebSearchConfigurationResponse:
    if web_search_environment_configured(settings):
        raise HTTPException(
            status_code=409,
            detail="Environment web-search configuration cannot be replaced in the UI",
        )
    try:
        base_url = validate_web_search_base_url(payload.base_url)
    except WebSearchConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = await session.get(WebSearchConfiguration, 1)
    if stored is None:
        if payload.api_key is None:
            raise HTTPException(status_code=422, detail="Tavily API Key is required")
        stored = WebSearchConfiguration(
            id=1,
            provider="tavily",
            base_url=base_url,
            api_key_encrypted=encrypt_web_search_key(
                payload.api_key.get_secret_value(), settings
            ),
            max_results=payload.max_results,
            search_depth=payload.search_depth,
        )
        session.add(stored)
    else:
        endpoint_changed = stored.base_url != base_url
        if endpoint_changed and payload.api_key is None:
            raise HTTPException(
                status_code=422,
                detail="Enter the Tavily API Key again when changing Base URL",
            )
        stored.base_url = base_url
        stored.max_results = payload.max_results
        stored.search_depth = payload.search_depth
        if payload.api_key:
            stored.api_key_encrypted = encrypt_web_search_key(
                payload.api_key.get_secret_value(), settings
            )
    await session.commit()
    return WebSearchConfigurationResponse(
        configured=True,
        provider="tavily",
        base_url=stored.base_url,
        has_api_key=True,
        max_results=stored.max_results,
        search_depth=stored.search_depth,
        source="local",
    )


@router.post("/web-search/test", response_model=WebSearchConnectionTestResponse)
async def test_web_search_configuration(
    payload: WebSearchConfigurationRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> WebSearchConnectionTestResponse:
    try:
        key = payload.api_key.get_secret_value() if payload.api_key else None
        if key is None:
            resolved = await resolve_web_search_settings(session, settings)
            candidate_url = validate_web_search_base_url(payload.base_url)
            if resolved is None or resolved.base_url != candidate_url:
                raise WebSearchConfigurationError(
                    "Enter the Tavily API Key before testing a changed Base URL"
                )
            key = resolved.api_key
        candidate = WebSearchSettings(
            provider="tavily",
            base_url=validate_web_search_base_url(payload.base_url),
            api_key=key,
            max_results=payload.max_results,
            search_depth=payload.search_depth,
            source="candidate",
        )
        await probe_web_search(candidate)
    except WebSearchConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WebSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return WebSearchConnectionTestResponse(detail="Tavily connection succeeded")


@router.delete("/web-search", status_code=204)
async def delete_web_search_configuration(
    session: SessionDependency,
    settings: SettingsDependency,
) -> Response:
    if web_search_environment_configured(settings):
        raise HTTPException(
            status_code=409,
            detail="Environment web-search configuration cannot be removed in the UI",
        )
    stored = await session.get(WebSearchConfiguration, 1)
    if stored is not None:
        await session.delete(stored)
        await session.commit()
    return Response(status_code=204)