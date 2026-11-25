"""Módulo de comunicação HTTP assíncrona com a API-DB (MetamorphIA_DB-API).

Centraliza toda leitura e persistência de alunos, turmas, conversas,
perfil_ia e arquivos de PEI (Supabase Storage via API-DB) em modo fail-closed.
"""

from __future__ import annotations

from typing import Any, NoReturn, Optional
from urllib.parse import quote
from fastapi import HTTPException, status
import httpx

from config import get_settings
from schemas import AlunoDB, TurmaDB


class DBClientError(Exception):
    """Exceção base para falhas de comunicação com a API-DB."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ResourceNotFoundError(DBClientError):
    """Lançada quando um recurso solicitado (aluno/turma/PEI) não existe na API-DB."""

    def __init__(self, message: str):
        super().__init__(message, status_code=404)


class DBForbiddenError(DBClientError):
    """Lançada quando a API-DB nega permissão (HTTP 403) ao usuário autenticado."""

    def __init__(self, message: str):
        super().__init__(message, status_code=403)


class DBUnauthorizedError(DBClientError):
    """Lançada quando o token é rejeitado (HTTP 401) pela API-DB."""

    def __init__(self, message: str):
        super().__init__(message, status_code=401)


def levantar_http_de_erro_db(exc: Exception, msg_nao_encontrado: str = "Recurso não encontrado.") -> NoReturn:
    """Converte exceções do cliente da API-DB em HTTPException fail-closed."""
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, ResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg_nao_encontrado) from exc
    if isinstance(exc, DBForbiddenError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para acessar ou alterar os dados deste aluno.",
        ) from exc
    if isinstance(exc, DBUnauthorizedError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas ou expiradas na API-DB.",
        ) from exc
    codigo = getattr(exc, "status_code", None)
    if codigo == 429 or "HTTP 429" in str(exc):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Limite de requisições excedido ao consultar a API-DB.",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Não foi possível comunicar ou validar permissões junto à API-DB.",
    ) from exc


def _montar_headers(token: Optional[str] = None) -> dict[str, str]:
    """Prepara os headers HTTP propagando exclusivamente o token Bearer do usuário."""
    headers = {"Content-Type": "application/json"}
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _obter_base_url(base_url: Optional[str] = None) -> str:
    """Retorna a URL base normalizada da API-DB."""
    settings = get_settings()
    return (base_url or settings.DB_API_URL).rstrip("/")


def _verificar_status_resposta(resposta: httpx.Response, contexto: str) -> None:
    if resposta.status_code == 404:
        raise ResourceNotFoundError(f"{contexto}: não encontrado na API-DB")
    if resposta.status_code == 403:
        raise DBForbiddenError(f"{contexto}: HTTP 403 - acesso negado pela API-DB")
    if resposta.status_code == 401:
        raise DBUnauthorizedError(f"{contexto}: HTTP 401 - não autenticado na API-DB")
    if not resposta.is_success:
        raise DBClientError(
            f"Erro na API-DB ({contexto}): HTTP {resposta.status_code}",
            status_code=resposta.status_code,
        )


async def obter_aluno(
    id_aluno: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> AlunoDB:
    """Busca os dados cadastrais, perfil_ia e status de PEI de um aluno na API-DB."""
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Aluno {id_aluno}")
    return AlunoDB.model_validate(resposta.json())


async def obter_turma(
    id_turma: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> TurmaDB:
    """Busca as informações de uma turma na API-DB pelo seu ID."""
    url = f"{_obter_base_url(base_url)}/turma/{int(id_turma)}"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar turma {id_turma}") from exc

    _verificar_status_resposta(resposta, f"Turma {id_turma}")
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
            resposta = await client.get(url, params={"id_turma": int(id_turma)}, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar alunos da turma {id_turma}") from exc

    _verificar_status_resposta(resposta, f"Alunos da turma {id_turma}")
    dados = resposta.json()
    if isinstance(dados, list):
        return [AlunoDB.model_validate(item) for item in dados]
    return []


async def atualizar_descricao_aluno(
    id_aluno: int,
    desc_aluno: Optional[str] = None,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
    *,
    perfil_ia: Optional[str] = None,
    indicadores_desenvolvimento: Optional[list[dict[str, Any]]] = None,
    metas_bimestre: Optional[list[dict[str, Any]]] = None,
    pei_feito: Optional[bool] = None,
    renovar_validade_pei_dias: Optional[int] = None,
) -> dict[str, Any]:
    """Atualiza desc_aluno, perfil_ia, indicadores e/ou validade do PEI na API-DB com sanitização LGPD."""
    from guardrails import sanitizar_dados_sensiveis

    corpo: dict[str, Any] = {}
    if desc_aluno is not None:
        desc_limpa, _ = sanitizar_dados_sensiveis(desc_aluno)
        corpo["desc_aluno"] = desc_limpa
    if perfil_ia is not None:
        perfil_limpo, _ = sanitizar_dados_sensiveis(perfil_ia)
        corpo["perfil_ia"] = perfil_limpo
    if indicadores_desenvolvimento is not None:
        corpo["indicadores_desenvolvimento"] = indicadores_desenvolvimento
    if metas_bimestre is not None:
        corpo["metas_bimestre"] = metas_bimestre
    if pei_feito is not None:
        corpo["pei_feito"] = bool(pei_feito)
    if renovar_validade_pei_dias is not None:
        corpo["renovar_validade_pei_dias"] = int(renovar_validade_pei_dias)

    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/descricao"
    headers = _montar_headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.patch(url, json=corpo, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao atualizar contexto do aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Atualização do aluno {id_aluno}")
    return resposta.json()


async def salvar_pei_arquivo_db(
    id_aluno: int,
    pei_id: str,
    arquivo_nome: str,
    documento_docx_base64: str,
    perfil_ia: Optional[str] = None,
    desc_aluno: Optional[str] = None,
    componente_curricular: Optional[str] = None,
    indicadores_desenvolvimento: Optional[list[dict[str, Any]]] = None,
    metas_bimestre: Optional[list[dict[str, Any]]] = None,
    validade_dias: int = 60,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Salva o .docx do PEI no Supabase Storage via API-DB e ativa pei_feito + validade de 60 dias."""
    from guardrails import sanitizar_dados_sensiveis

    corpo: dict[str, Any] = {
        "pei_id": pei_id,
        "arquivo_nome": arquivo_nome,
        "documento_docx_base64": documento_docx_base64,
        "validade_dias": int(validade_dias),
    }
    if componente_curricular is not None:
        corpo["componente_curricular"] = componente_curricular
    if perfil_ia is not None:
        corpo["perfil_ia"] = sanitizar_dados_sensiveis(perfil_ia)[0]
    if desc_aluno is not None:
        corpo["desc_aluno"] = sanitizar_dados_sensiveis(desc_aluno)[0]
    if indicadores_desenvolvimento is not None:
        corpo["indicadores_desenvolvimento"] = indicadores_desenvolvimento
    if metas_bimestre is not None:
        corpo["metas_bimestre"] = metas_bimestre

    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/pei-arquivo"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.post(url, json=corpo, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao salvar PEI do aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Salvar PEI do aluno {id_aluno}")
    return resposta.json()


async def atualizar_status_pei_disciplina_db(
    id_aluno: int,
    disciplina: str,
    status_pei: str = "em_andamento",
    pei_id: Optional[str] = None,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Atualiza o status do PEI de uma disciplina do aluno na API-DB."""
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/pei-disciplina"
    headers = _montar_headers(token)
    corpo: dict[str, Any] = {"disciplina": disciplina, "status": status_pei}
    if pei_id:
        corpo["pei_id"] = pei_id
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.post(url, json=corpo, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao atualizar PEI da disciplina {disciplina}") from exc
    _verificar_status_resposta(resposta, f"PEI disciplina {disciplina}")
    return resposta.json()


async def listar_peis_aluno_db(
    id_aluno: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Lista os PEIs salvos no Supabase Storage / banco para o aluno."""
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/pei-arquivos"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao listar PEIs do aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Listar PEIs do aluno {id_aluno}")
    dados = resposta.json()
    return dados if isinstance(dados, list) else []


async def obter_pei_detalhe_db(
    id_aluno: int,
    pei_id: str,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Obtém o detalhe textual de um PEI salvo no Supabase Storage via API-DB."""
    pei_seguro = quote(str(pei_id).strip(), safe="")
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/pei-arquivos/{pei_seguro}"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar PEI {pei_id}") from exc

    _verificar_status_resposta(resposta, f"PEI {pei_id}")
    return resposta.json()


async def baixar_pei_docx_db(
    id_aluno: int,
    pei_id: str,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 30.0,
) -> tuple[bytes, str]:
    """Baixa os bytes do .docx de um PEI salvo no Supabase Storage via API-DB."""
    pei_seguro = quote(str(pei_id).strip(), safe="")
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/pei-arquivos/{pei_seguro}/docx"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao baixar .docx do PEI {pei_id}") from exc

    _verificar_status_resposta(resposta, f"Download PEI {pei_id}")
    disp = resposta.headers.get(
        "content-disposition",
        f'attachment; filename="PEI_aluno_{int(id_aluno)}.docx"',
    )
    return resposta.content, disp


async def listar_conversas_aluno_db(
    id_aluno: int,
    incluir_metricas: bool = False,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> Any:
    """Lista conversas (e opcionalmente métricas pedagógicas) do aluno no PostgreSQL."""
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/conversas-db"
    headers = _montar_headers(token)
    params = {"incluir_metricas": "true"} if incluir_metricas else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao listar conversas do aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Conversas do aluno {id_aluno}")
    return resposta.json()


async def obter_mensagens_conversa_db(
    id_aluno: int,
    conversa_id: str,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Recupera as mensagens de uma conversa específica do professor no PostgreSQL."""
    cid_seguro = quote(str(conversa_id).strip(), safe="")
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/conversas-db/{cid_seguro}"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao buscar conversa {conversa_id}") from exc

    _verificar_status_resposta(resposta, f"Conversa {conversa_id}")
    dados = resposta.json()
    return dados.get("mensagens", []) if isinstance(dados, dict) else []


async def registrar_turno_conversa_db(
    id_aluno: int,
    conversa_id: str,
    mensagem_usuario: str,
    resposta_ia: str,
    titulo: Optional[str] = None,
    anexos_usuario: Optional[list[dict[str, Any]]] = None,
    anexos_ia: Optional[list[dict[str, Any]]] = None,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Persiste o turno de chat (usuário + IA) no PostgreSQL / Supabase Storage via API-DB."""
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/conversas-db"
    headers = _montar_headers(token)
    corpo = {
        "conversa_id": conversa_id,
        "titulo": titulo,
        "mensagem_usuario": mensagem_usuario,
        "resposta_ia": resposta_ia,
        "anexos_usuario": anexos_usuario,
        "anexos_ia": anexos_ia,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.post(url, json=corpo, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao registrar conversa do aluno {id_aluno}") from exc

    _verificar_status_resposta(resposta, f"Registrar conversa {conversa_id}")
    return resposta.json()


async def excluir_conversa_aluno_db(
    id_aluno: int,
    conversa_id: str,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Exclui uma conversa do professor no PostgreSQL / Supabase Storage via API-DB."""
    cid_seguro = quote(str(conversa_id).strip(), safe="")
    url = f"{_obter_base_url(base_url)}/Alunos/{int(id_aluno)}/conversas-db/{cid_seguro}"
    headers = _montar_headers(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resposta = await client.delete(url, headers=headers)
        except httpx.RequestError as exc:
            raise DBClientError(f"Falha de conexão ao excluir conversa {conversa_id}") from exc

    _verificar_status_resposta(resposta, f"Excluir conversa {conversa_id}")
    return resposta.json()


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
