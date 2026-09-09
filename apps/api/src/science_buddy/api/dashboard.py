from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import DashboardResponse
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import Project
from science_buddy.services.usage import UsageService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=DashboardResponse)
async def usage_dashboard(
    project_id: UUID,
    session: SessionDependency,
    period: Annotated[Literal["7d", "30d", "90d", "all"], Query()] = "30d",
    granularity: Annotated[Literal["day", "week", "month"], Query()] = "day",
) -> DashboardResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    values = await UsageService(session).dashboard(
        project_id,
        period=period,
        granularity=granularity,
    )
    return DashboardResponse(
        project_id=project_id,
        period=period,
        granularity=granularity,
        **values,
    )