"""Serviço de integração externa e comunicação com a API-DB."""

from __future__ import annotations

from db_client import (
    DBClientError,
    DBForbiddenError,
    DBUnauthorizedError,
    ResourceNotFoundError,
    atualizar_descricao_aluno,
    baixar_pei_docx_db,
    excluir_conversa_aluno_db,
    levantar_http_de_erro_db,
    listar_conversas_aluno_db,
    listar_peis_aluno_db,
    obter_aluno,
    obter_alunos_por_turma,
    obter_mensagens_conversa_db,
    obter_pei_detalhe_db,
    obter_turma,
    registrar_turno_conversa_db,
    salvar_pei_arquivo_db,
)

__all__ = [
    "DBClientError",
    "DBForbiddenError",
    "DBUnauthorizedError",
    "ResourceNotFoundError",
    "levantar_http_de_erro_db",
    "obter_aluno",
    "obter_turma",
    "obter_alunos_por_turma",
    "atualizar_descricao_aluno",
    "salvar_pei_arquivo_db",
    "listar_peis_aluno_db",
    "obter_pei_detalhe_db",
    "baixar_pei_docx_db",
    "listar_conversas_aluno_db",
    "obter_mensagens_conversa_db",
    "registrar_turno_conversa_db",
    "excluir_conversa_aluno_db",
]
