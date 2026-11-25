"""Módulo de persistência e gerenciamento do banco vetorial ChromaDB.

Restrito exclusivamente aos documentos científicos e legais de referência
(DUA, Decreto 12.686, Guias WWC). Dados e memória de estudantes ficam
exclusivamente no banco relacional (API-DB) e no Supabase Storage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import chromadb
from config import get_settings


def obter_cliente_chroma(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    """Abre ou cria o cliente ChromaDB persistente no caminho configurado."""
    caminho = Path(persist_directory or get_settings().CHROMA_PATH)
    caminho.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(caminho))


def obter_colecao_documentos(persist_directory: Optional[str] = None):
    """Retorna a coleção contendo os chunks dos PDFs de referência (DUA, Leis, WWC)."""
    settings = get_settings()
    cliente = obter_cliente_chroma(persist_directory)
    return cliente.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_DOCS,
        metadata={"hnsw:space": "cosine", "tipo": "documentos_referencia"},
    )


def descartar_colecao_memoria_legada(persist_directory: Optional[str] = None) -> None:
    """Remove eventual coleção legada de memória de alunos caso exista em disco."""
    settings = get_settings()
    nome_legado = getattr(settings, "CHROMA_COLLECTION_MEMORIA", "metamorphia_memoria")
    if not nome_legado or nome_legado == settings.CHROMA_COLLECTION_DOCS:
        return
    try:
        cliente = obter_cliente_chroma(persist_directory)
        nomes = [
            c if isinstance(c, str) else getattr(c, "name", "")
            for c in cliente.list_collections()
        ]
        if nome_legado in nomes:
            cliente.delete_collection(name=nome_legado)
    except Exception:
        pass


def get_chroma_client(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    return obter_cliente_chroma(persist_directory)
