"""Aplicação FastAPI Principal - MetamorphIA API-IA.

Serviço de Inteligência Artificial para apoio pedagógico inclusivo,
adaptação de atividades curriculares e estruturação de Planos Educacionais
Individualizados (PEIs) para estudantes neurodivergentes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import io
from pathlib import Path
import zipfile

from fastapi import FastAPI, Request, Response
import httpx
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from api.routes.chat import router as chat_router
from api.routes.dashboard_aluno import router as dashboard_aluno_router
from api.routes.mock import router as mock_router
from api.routes.pei import router as pei_router
from api.routes.query import Queryrouter as query_router
from config import get_settings

_AMBIENTES_DEV_HOMOLOG = {"development", "dev", "test", "testing", "homologacao", "homolog", "staging", "local"}

settings = get_settings()
eh_producao = settings.ENVIRONMENT.strip().lower() not in _AMBIENTES_DEV_HOMOLOG
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])


def _baixar_e_extrair_asset_supabase(object_name: str, destino_dir: Path) -> None:
    url = (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
        f"{settings.SUPABASE_BUCKET}/system_assets/{object_name}"
    )
    key = settings.SUPABASE_SERVICE_KEY.strip()
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    resp = httpx.get(url, headers=headers, timeout=120.0)
    if not resp.is_success:
        print(f"[Aviso] Não foi possível baixar {object_name} do Supabase: {resp.status_code}")
        return
    destino_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
        for member in zf.infolist():
            alvo = (destino_dir / member.filename).resolve()
            if not str(alvo).startswith(str(destino_dir.resolve())):
                continue
            if member.is_dir():
                alvo.mkdir(parents=True, exist_ok=True)
            else:
                alvo.parent.mkdir(parents=True, exist_ok=True)
                alvo.write_bytes(zf.read(member.filename))
    print(f"[Startup] Asset {object_name} restaurado em {destino_dir}.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_settings()
    if (
        cfg.ENVIRONMENT.strip().lower() not in ("test", "testing")
        and cfg.SUPABASE_URL.strip()
        and cfg.SUPABASE_SERVICE_KEY.strip()
    ):
        chroma_dir = Path(cfg.CHROMA_PATH)
        if not (chroma_dir / "chroma.sqlite3").is_file():
            try:
                _baixar_e_extrair_asset_supabase("chroma.zip", chroma_dir)
            except Exception as exc:
                print(f"[Aviso] Falha ao restaurar chroma.zip: {exc}")

        pei_dir = Path(__file__).resolve().parent / "PEI-estilo"
        if not list(pei_dir.glob("*.docx")):
            try:
                _baixar_e_extrair_asset_supabase("pei_estilo.zip", pei_dir)
            except Exception as exc:
                print(f"[Aviso] Falha ao restaurar pei_estilo.zip: {exc}")
    yield


app = FastAPI(
    title="MetamorphIA - API-IA",
    description="Serviço de Inteligência Artificial para apoio pedagógico inclusivo e adaptação curricular",
    version="2.0.0",
    docs_url=None if eh_producao else "/docs",
    redoc_url=None if eh_producao else "/redoc",
    openapi_url=None if eh_producao else "/openapi.json",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

cors_origins = [
    o.strip()
    for o in settings.CORS_ORIGINS.split(",")
    if o.strip() and o.strip() != "*"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def adicionar_headers_seguranca(request: Request, call_next) -> Response:
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# Registro de todas as rotas ativas do serviço de IA
app.include_router(chat_router)
app.include_router(dashboard_aluno_router)
app.include_router(pei_router)
if not eh_producao:
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