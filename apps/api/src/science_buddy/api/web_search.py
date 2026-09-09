from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    ControlledWebSearchItem,
    ControlledWebSearchRequest,
    ControlledWebSearchResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.infrastructure.database import get_session
from science_buddy.services.web_search import (
    TavilySearchService,
    WebSearchConfigurationError,
    WebSearchError,
    resolve_web_search_settings,
)

router = APIRouter(prefix="/web-search", tags=["web-search"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post("/search", response_model=ControlledWebSearchResponse)
async def controlled_web_search(
    payload: ControlledWebSearchRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> ControlledWebSearchResponse:
    resolved = await resolve_web_search_settings(session, settings)
    if resolved is None:
        raise HTTPException(status_code=503, detail="Configure Tavily web search first")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            results = await TavilySearchService(client, resolved).search(
                payload.query,
                max_results=payload.max_results,
            )
    except WebSearchConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WebSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ControlledWebSearchResponse(
        query=payload.query,
        items=[
            ControlledWebSearchItem(
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                score=item.score,
                published_date=item.published_date,
            )
            for item in results
        ],
    )