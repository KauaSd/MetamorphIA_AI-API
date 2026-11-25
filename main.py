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
from auth_jwt import decodificar_token_jwt
from config import get_settings

_AMBIENTES_DEV_HOMOLOG = {"development", "dev", "test", "testing", "homologacao", "homolog", "staging", "local"}
_ASSETS_PERMITIDOS_SUPABASE = {"chroma.zip", "pei_estilo.zip"}

settings = get_settings()
eh_producao = settings.ENVIRONMENT.strip().lower() not in _AMBIENTES_DEV_HOMOLOG


def _obter_chave_rate_limit(request: Request) -> str:
    """Chaveia o rate limit pelo sub do JWT validado para evitar colisão de IP em proxies/gateways."""
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        token_raw = auth_header[7:].strip()
        if token_raw:
            try:
                payload = decodificar_token_jwt(token_raw)
                sub = payload.get("sub") or payload.get("id_prof")
                if sub is not None:
                    return f"user:{sub}"
            except Exception:
                pass
    return get_remote_address(request)


limiter = Limiter(key_func=_obter_chave_rate_limit, default_limits=[settings.RATE_LIMIT_DEFAULT])


_MAX_ASSET_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_MAX_ASSET_EXTRACTED_BYTES = 250 * 1024 * 1024  # 250 MB
_MAX_ASSET_ENTRIES = 5000


def _baixar_e_extrair_asset_supabase(object_name: str, destino_dir: Path) -> None:
    if object_name not in _ASSETS_PERMITIDOS_SUPABASE:
        raise ValueError(f"Asset não autorizado: {object_name}")
    url = (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
        f"{settings.SUPABASE_BUCKET}/system_assets/{object_name}"
    )
    key = (settings.SUPABASE_ASSETS_KEY or settings.SUPABASE_SERVICE_KEY).strip()
    if not key:
        return
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    resp = httpx.get(url, headers=headers, timeout=120.0)
    if not resp.is_success:
        print(f"[Aviso] Não foi possível baixar {object_name} do Supabase: {resp.status_code}")
        return
    if len(resp.content) > _MAX_ASSET_DOWNLOAD_BYTES:
        raise ValueError(f"Asset {object_name} excede o limite máximo de download.")

    destino_resolvido = destino_dir.resolve()
    destino_resolvido.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ASSET_ENTRIES or sum(i.file_size for i in infos) > _MAX_ASSET_EXTRACTED_BYTES:
            raise ValueError(f"Asset {object_name} excede os limites seguros de extração.")
        for member in infos:
            alvo = (destino_resolvido / member.filename).resolve()
            if not alvo.is_relative_to(destino_resolvido):
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
        and (cfg.SUPABASE_ASSETS_KEY.strip() or cfg.SUPABASE_SERVICE_KEY.strip())
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
    try:
        from memoria.chroma_client import descartar_colecao_memoria_legada

        descartar_colecao_memoria_legada(cfg.CHROMA_PATH)
    except Exception:
        pass
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
    o.strip().rstrip("/")
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
if not eh_producao and settings.ENABLE_MOCK_ROUTES:
    app.include_router(mock_router)
app.include_router(query_router)


@app.get("/health", tags=["health"])
async def health() -> Response:
    """Endpoint de verificação de integridade com checagem real da API-DB e ChromaDB."""
    import json

    cfg = get_settings()
    db_status = "unreachable"
    chroma_status = "error"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{cfg.DB_API_URL.rstrip('/')}/health")
            if resp.is_success:
                db_status = "ok"
    except Exception:
        db_status = "unreachable"

    try:
        from memoria.chroma_client import obter_colecao_documentos

        obter_colecao_documentos(cfg.CHROMA_PATH).count()
        chroma_status = "ok"
    except Exception:
        chroma_status = "error"

    saudavel = db_status == "ok" and chroma_status == "ok"
    payload = {
        "status": "ok" if saudavel else "degraded",
        "service": "api-ia",
        "api_db": db_status,
        "chroma_documentos": chroma_status,
        "llm_provider": cfg.LLM_PROVIDER,
        "auth_security": "jwt_bearer_enabled",
    }
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=200 if saudavel else 503,
    )