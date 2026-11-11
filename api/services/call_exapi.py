"""Serviço de integração externa e comunicação com a API-DB."""

from __future__ import annotations

from db_client import (
    DBClientError,
    ResourceNotFoundError,
    atualizar_descricao_aluno,
    obter_aluno,
    obter_alunos_por_turma,
    obter_turma,
)

__all__ = [
    "DBClientError",
    "ResourceNotFoundError",
    "obter_aluno",
    "obter_turma",
    "obter_alunos_por_turma",
    "atualizar_descricao_aluno",
]