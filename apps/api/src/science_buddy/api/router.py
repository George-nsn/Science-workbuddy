from fastapi import APIRouter

from science_buddy.api.brainstorm import router as brainstorm_router
from science_buddy.api.dashboard import router as dashboard_router
from science_buddy.api.documents import router as documents_router
from science_buddy.api.graph import router as graph_router
from science_buddy.api.health import router as health_router
from science_buddy.api.literature import router as literature_router
from science_buddy.api.memory import router as memory_router
from science_buddy.api.rag import router as rag_router
from science_buddy.api.research import router as research_router
from science_buddy.api.settings import router as settings_router
from science_buddy.api.synthesis import router as synthesis_router
from science_buddy.api.tools import router as tools_router
from science_buddy.api.web_search import router as web_search_router
from science_buddy.api.workbench import router as workbench_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(literature_router)
api_router.include_router(documents_router)
api_router.include_router(research_router)
api_router.include_router(graph_router)
api_router.include_router(rag_router)
api_router.include_router(brainstorm_router)
api_router.include_router(dashboard_router)
api_router.include_router(settings_router)
api_router.include_router(synthesis_router)
api_router.include_router(workbench_router)
api_router.include_router(memory_router)
api_router.include_router(web_search_router)
api_router.include_router(tools_router)
