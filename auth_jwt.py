from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt.exceptions import InvalidTokenError

from config import get_settings

security_bearer = HTTPBearer(auto_error=True)


def decodificar_token_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except InvalidTokenError as erro:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Não foi possível validar as credenciais: {erro}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from erro


def verificar_token_jwt(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_bearer)],
) -> dict[str, Any]:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decodificar_token_jwt(credentials.credentials)
    id_prof = payload.get("sub") or payload.get("id_prof")
    if id_prof is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido: identificador de usuário ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def validar_permissao_chat_usuario(payload: dict[str, Any]) -> None:
    tipo_usuario = payload.get("tipo_usuario", "normal")
    pode_chat = payload.get("pode_chat", True)

    if tipo_usuario == "adm_institucional":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="O administrador institucional não pode acessar ou conversar no chat dos alunos.",
        )
    if pode_chat is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este perfil de administrador Pro+ está configurado sem acesso ao chat dos alunos.",
        )


def criar_token_teste(
    id_prof: int = 1,
    expira_em_minutos: int = 60,
    dados_extras: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    expiracao = datetime.now(timezone.utc) + timedelta(minutes=expira_em_minutos)
    payload: dict[str, Any] = {
        "sub": str(id_prof),
        "id_prof": id_prof,
        "is_verified": True,
        "exp": expiracao,
    }
    if dados_extras:
        payload.update(dados_extras)

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
