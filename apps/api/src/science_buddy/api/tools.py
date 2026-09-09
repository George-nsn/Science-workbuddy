from fastapi import APIRouter

from science_buddy.api.schemas import ToolRegistrationResponse, ToolRegistryResponse
from science_buddy.services.tool_registry import list_tool_registrations

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/registry", response_model=ToolRegistryResponse)
async def get_tool_registry() -> ToolRegistryResponse:
    return ToolRegistryResponse(
        tools=[
            ToolRegistrationResponse(
                tool_id=value.tool_id,
                label=value.label,
                provider=value.provider,
                input_schema=value.input_schema,
                permission=value.permission,
                cost_class=value.cost_class,
                timeout_seconds=value.timeout_seconds,
                evidence_eligible=value.evidence_eligible,
                mechanical_verification=value.mechanical_verification,
                enabled=value.enabled,
                implementation_status=value.implementation_status,
            )
            for value in list_tool_registrations()
        ]
    )
