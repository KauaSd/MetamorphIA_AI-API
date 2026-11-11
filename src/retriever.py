"""Busca semântica dos trechos documentais de referência para apoio pedagógico."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from memoria.chroma_client import obter_colecao_documentos


@dataclass(frozen=True)
class TrechoRecuperado:
    texto: str
    metadata: dict[str, Any]
    distancia: float | None


def buscar_trechos(pergunta: str, quantidade: int = 4) -> list[TrechoRecuperado]:
    """Retorna os trechos documentais semanticamente mais próximos da consulta."""
    if not pergunta or not pergunta.strip():
        return []

    from guardrails import sanitizar_dados_sensiveis
    pergunta_limpa, _ = sanitizar_dados_sensiveis(pergunta)

    colecao = obter_colecao_documentos()
    total = colecao.count()
    if total == 0:
        return []

    resultado = colecao.query(
        query_texts=[pergunta_limpa],
        n_results=min(quantidade, total),
        include=["documents", "metadatas", "distances"],
    )

    docs = resultado.get("documents", [[]])[0]
    metas = resultado.get("metadatas", [[]])[0]
    dists = resultado.get("distances", [[]])[0] if "distances" in resultado else [None] * len(docs)

    return [
        TrechoRecuperado(texto=texto, metadata=meta or {}, distancia=dist)
        for texto, meta, dist in zip(docs, metas, dists)
    ]
