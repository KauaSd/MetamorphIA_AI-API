"""Módulo de comunicação HTTP assíncrona com a API-DB (MetamorphIA_DB-API).

Implementa funções puras para consulta e atualização de alunos e turmas.
Adota estilo funcional, com tratamento de exceções tipadas e gerenciamento
limpo de requisições assíncronas via httpx.
"""

from __future__ import annotations

from typing import Any, Optional
import httpx

from config import get_settings
from schemas import AlunoDB, TurmaDB


class DBClientError(Exception):
    """Exceção base para falhas de comunicação com a API-DB."""


class ResourceNotFoundError(DBClientError):
    """Lançada quando um recurso solicitado (aluno/turma) não existe na API-DB."""


def _montar_headers(token: Optional[str] = None) -> dict[str, str]:
    """Prepara os headers HTTP incluindo o token Bearer de autenticação, se presente."""
    settings = get_settings()
    auth_token = token or settings.API_DB_AUTH_TOKEN
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _obter_base_url(base_url: Optional[str] = None) -> str:
    """Retorna a URL base normalizada da API-DB."""
    settings = get_settings()
    return (base_url or settings.DB_API_URL).rstrip("/")


async def obter_aluno(
    id_aluno: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> AlunoDB:
    """Busca os dados cadastrais de um aluno na API-DB pelo seu ID."""
    url = f"{_obter_base_url(base_url)}/Alunos/{id_aluno}"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar aluno {id_aluno}: {exc}") from exc

    if resposta.status_code == 404:
        raise ResourceNotFoundError(f"Aluno {id_aluno} não encontrado na API-DB")
    if not resposta.is_success:
        raise DBClientError(
            f"Erro na API-DB ao buscar aluno {id_aluno}: HTTP {resposta.status_code} - {resposta.text}"
        )

    return AlunoDB.model_validate(resposta.json())


async def obter_turma(
    id_turma: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> TurmaDB:
    """Busca as informações de uma turma na API-DB pelo seu ID."""
    url = f"{_obter_base_url(base_url)}/turma/{id_turma}"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar turma {id_turma}: {exc}") from exc

    if resposta.status_code == 404:
        raise ResourceNotFoundError(f"Turma {id_turma} não encontrada na API-DB")
    if not resposta.is_success:
        raise DBClientError(
            f"Erro na API-DB ao buscar turma {id_turma}: HTTP {resposta.status_code} - {resposta.text}"
        )

    return TurmaDB.model_validate(resposta.json())


async def obter_alunos_por_turma(
    id_turma: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> list[AlunoDB]:
    """Lista todos os alunos vinculados a uma turma específica."""
    url = f"{_obter_base_url(base_url)}/Alunos"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, params={"id_turma": id_turma}, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(
                f"Falha de conexão ao buscar alunos da turma {id_turma}: {exc}"
            ) from exc

    if not resposta.is_success:
        raise DBClientError(
            f"Erro na API-DB ao buscar alunos da turma {id_turma}: HTTP {resposta.status_code}"
        )

    dados = resposta.json()
    if isinstance(dados, list):
        return [AlunoDB.model_validate(item) for item in dados]
    return []


async def atualizar_descricao_aluno(
    id_aluno: int,
    desc_aluno: str,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Atualiza o perfil pedagógico (descrição) de um aluno na API-DB.

    Garante previamente que CPFs ou dados pessoais sensíveis não sejam
    persistidos no banco de dados relacional (LGPD).
    """
    from guardrails import sanitizar_dados_sensiveis

    desc_limpa, _ = sanitizar_dados_sensiveis(desc_aluno)
    url = f"{_obter_base_url(base_url)}/Alunos/{id_aluno}/descricao"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.patch(url, json={"desc_aluno": desc_limpa}, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(
                f"Falha de conexão ao atualizar descrição do aluno {id_aluno}: {exc}"
            ) from exc

    if resposta.status_code == 404:
        raise ResourceNotFoundError(f"Aluno {id_aluno} não encontrado para atualização")
    if not resposta.is_success:
        raise DBClientError(
            f"Erro na API-DB ao atualizar descrição: HTTP {resposta.status_code} - {resposta.text}"
        )

    return resposta.json()


# Classe de compatibilidade retroativa para código legado que possa chamar DBClient()
class DBClient:
    """Wrapper funcional mantido para compatibilidade retroativa."""

    def __init__(self, base_url: Optional[str] = None, auth_token: Optional[str] = None):
        self.base_url = base_url
        self.auth_token = auth_token

    async def close(self) -> None:
        pass

    async def get_aluno(self, id_aluno: int) -> AlunoDB:
        return await obter_aluno(id_aluno, token=self.auth_token, base_url=self.base_url)

    async def get_turma(self, id_turma: int) -> TurmaDB:
        return await obter_turma(id_turma, token=self.auth_token, base_url=self.base_url)

    async def get_alunos_por_turma(self, id_turma: int) -> list[AlunoDB]:
        return await obter_alunos_por_turma(id_turma, token=self.auth_token, base_url=self.base_url)

    async def patch_desc_aluno(self, id_aluno: int, desc_aluno: str) -> dict[str, Any]:
        return await atualizar_descricao_aluno(
            id_aluno, desc_aluno, token=self.auth_token, base_url=self.base_url
        )