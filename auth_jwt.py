from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
import jwt
from jwt.exceptions import InvalidTokenError

from cache_utils import BoundedTTLCache
from config import get_settings
from db_client import obter_cliente_http

security_bearer = HTTPBearer(auto_error=True)
_CACHE_SESSAO_DB: BoundedTTLCache[str, dict[str, Any]] = BoundedTTLCache(
    ttl_seconds=120, max_items=512
)


def decodificar_token_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except InvalidTokenError as erro:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não foi possível validar as credenciais fornecidas.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from erro


async def _validar_sessao_api_db(token_raw: str) -> dict[str, Any]:
    """Revalida na API-DB o usuário ativo e permissões, com cache em memória por token (TTL configurável)."""
    settings = get_settings()
    eh_teste = settings.ENVIRONMENT.strip().lower() in ("test", "testing")
    ttl_cache = 0 if eh_teste else max(0, int(getattr(settings, "JWT_SESSION_CACHE_TTL_SECONDS", 120)))
    chave_cache = hashlib.sha256(token_raw.encode("utf-8")).hexdigest()
    if ttl_cache > 0:
        _CACHE_SESSAO_DB.ttl_seconds = ttl_cache
        em_cache = _CACHE_SESSAO_DB.get(chave_cache)
        if em_cache is not None:
            return em_cache

    url = f"{settings.DB_API_URL.rstrip('/')}/autenticar/me"
    headers = {"Authorization": f"Bearer {token_raw}"}
    async with obter_cliente_http(10.0) as client:
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Serviço de autenticação (API-DB) temporariamente indisponível.",
            ) from exc

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou revogada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if resp.status_code == status.HTTP_403_FORBIDDEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário sem permissão para acessar este recurso.",
        )
    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao validar sessão na API-DB.",
        )
    dados = resp.json()
    resultado = dados if isinstance(dados, dict) else {}
    if ttl_cache > 0 and resultado:
        _CACHE_SESSAO_DB[chave_cache] = resultado
    return resultado


async def verificar_token_jwt(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_bearer)],
) -> dict[str, Any]:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decodificar_token_jwt(credentials.credentials)
    proposito = payload.get("purpose")
    if proposito and proposito != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tipo de token inválido para acesso à API.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    id_prof = payload.get("sub") or payload.get("id_prof")
    if id_prof is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido: identificador de usuário ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("is_verified") is not True or payload.get("2fa"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token não verificado ou pendente de segundo fator (2FA)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    perfil_db = await _validar_sessao_api_db(credentials.credentials)
    id_usuario_db = perfil_db.get("id_usuario") or id_prof
    email_atual = (perfil_db.get("email_usuario") or "").strip().lower()
    tipo_atual = perfil_db.get("tipo_usuario") or "normal"
    pode_chat_atual = bool(perfil_db.get("pode_chat", False))
    nome_atual = perfil_db.get("nome_usuario") or ""

    settings = get_settings()
    permitidos_raw = (getattr(settings, "AI_ALLOWED_EMAILS", "") or "").strip()
    if permitidos_raw:
        permitidos = {e.strip().lower() for e in permitidos_raw.split(",") if e.strip()}
        if not email_atual or email_atual not in permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="O acesso à IA está temporariamente restrito à conta autorizada.",
            )

    payload["sub"] = str(id_usuario_db)
    payload["id_prof"] = int(id_usuario_db)
    payload["email"] = email_atual
    payload["tipo_usuario"] = tipo_atual
    payload["pode_chat"] = pode_chat_atual
    payload["nome_prof"] = nome_atual
    payload["tags"] = perfil_db.get("tags") or []
    payload["_raw_token"] = credentials.credentials
    return payload


def validar_permissao_chat_usuario(payload: dict[str, Any]) -> None:
    tipo_usuario = payload.get("tipo_usuario", "normal")
    pode_chat = payload.get("pode_chat", False)

    if tipo_usuario == "adm_institucional":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="O administrador institucional não pode acessar ou conversar no chat dos alunos.",
        )
    if pode_chat is not True and tipo_usuario != "adm_sistema":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este perfil está configurado sem acesso ao chat dos alunos.",
        )


def criar_token_teste(
    id_prof: int = 1,
    expira_em_minutos: int = 60,
    dados_extras: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    if settings.ENVIRONMENT.strip().lower() not in ("test", "testing"):
        raise RuntimeError("criar_token_teste é permitido exclusivamente em ambiente de testes.")

    expiracao = datetime.now(timezone.utc) + timedelta(minutes=expira_em_minutos)
    payload: dict[str, Any] = {
        "sub": str(id_prof),
        "id_prof": id_prof,
        "is_verified": True,
        "pode_chat": True,
        "purpose": "access",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": expiracao,
    }
    if dados_extras:
        payload.update(dados_extras)

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
