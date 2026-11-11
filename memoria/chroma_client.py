"""Módulo de persistência e gerenciamento do banco vetorial ChromaDB.

Gerencia as coleções:
1. Memória individual e interações do aluno (conversas, PEIs, perfis).
2. Documentos científicos e legais de referência (DUA, Decreto 12.686, Guias WWC).

Estilo puramente funcional, sem classes desnecessárias.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import chromadb
from config import get_settings


def obter_cliente_chroma(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    """Abre ou cria o cliente ChromaDB persistente no caminho configurado."""
    caminho = Path(persist_directory or get_settings().CHROMA_PATH)
    caminho.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(caminho))


def obter_colecao_memoria(persist_directory: Optional[str] = None):
    """Retorna a coleção de memória pedagógica e histórico de alunos."""
    settings = get_settings()
    cliente = obter_cliente_chroma(persist_directory)
    return cliente.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_MEMORIA,
        metadata={"hnsw:space": "cosine", "tipo": "memoria_aluno"},
    )


def obter_colecao_documentos(persist_directory: Optional[str] = None):
    """Retorna a coleção contendo os chunks dos PDFs de referência (DUA, Leis, WWC)."""
    settings = get_settings()
    cliente = obter_cliente_chroma(persist_directory)
    return cliente.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_DOCS,
        metadata={"hnsw:space": "cosine", "tipo": "documentos_referencia"},
    )


# Alias para manter compatibilidade com módulos legados
def get_chroma_client(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    return obter_cliente_chroma(persist_directory)


def get_chroma_collection(persist_directory: Optional[str] = None):
    return obter_colecao_memoria(persist_directory)


def salvar_documento(
    id_aluno: int,
    id_turma: int,
    id_prof: int,
    tipo: str,
    conteudo: str,
    conversa_id: Optional[str] = None,
    titulo: Optional[str] = None,
    id_doc: Optional[str] = None,
    data_iso: Optional[str] = None,
    persist_dir: Optional[str] = None,
) -> str:
    """Insere ou atualiza um documento na memória do aluno no ChromaDB.

    Garante sanitização estrita de CPFs e dados sensíveis antes de qualquer gravação
    no banco vetorial, impedindo persistência de PII.
    """
    from guardrails import sanitizar_dados_sensiveis

    conteudo_limpo, _ = sanitizar_dados_sensiveis(conteudo)
    titulo_limpo = ""
    if titulo:
        titulo_limpo, _ = sanitizar_dados_sensiveis(titulo)

    doc_id = id_doc or str(uuid.uuid4())
    timestamp = data_iso or datetime.now(timezone.utc).isoformat()
    colecao = obter_colecao_memoria(persist_dir)

    metadata: dict[str, Any] = {
        "id_aluno": int(id_aluno),
        "id_turma": int(id_turma),
        "id_prof": int(id_prof),
        "tipo": str(tipo),
        "conversa_id": str(conversa_id or ""),
        "titulo": str(titulo_limpo),
        "data": str(timestamp),
    }

    colecao.upsert(
        ids=[doc_id],
        documents=[conteudo_limpo],
        metadatas=[metadata],
    )
    return doc_id
