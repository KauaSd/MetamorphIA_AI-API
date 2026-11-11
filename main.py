"""Aplicação FastAPI Principal - MetamorphIA API-IA.

Serviço de Inteligência Artificial para apoio pedagógico inclusivo,
adaptação de atividades curriculares e estruturação de Planos Educacionais
Individualizados (PEIs) para estudantes neurodivergentes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.chat import router as chat_router
from api.routes.dashboard_aluno import router as dashboard_aluno_router
from api.routes.mock import router as mock_router
from api.routes.pei import router as pei_router
from api.routes.query import Queryrouter as query_router
from config import get_settings

app = FastAPI(
    title="MetamorphIA - API-IA",
    description="Serviço de Inteligência Artificial para apoio pedagógico inclusivo e adaptação curricular",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registro de todas as rotas ativas do serviço de IA
app.include_router(chat_router)
app.include_router(dashboard_aluno_router)
app.include_router(pei_router)
app.include_router(mock_router)
app.include_router(query_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Endpoint de verificação de integridade e prontidão da API."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": "api-ia",
        "chroma_memoria": settings.CHROMA_COLLECTION_MEMORIA,
        "chroma_documentos": settings.CHROMA_COLLECTION_DOCS,
        "llm_provider": settings.LLM_PROVIDER,
        "auth_security": "jwt_bearer_enabled",
    }