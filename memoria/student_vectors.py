"""Gerador de embeddings locais (384d) e cliente vetorial por aluno (pgvector na API-DB)."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from typing import Any, Optional

from db_client import buscar_vetores_aluno_db, salvar_vetores_aluno_db
from guardrails import sanitizar_dados_sensiveis

_EMBEDDER_LOCAL: Any = None
_EMBEDDER_INICIALIZADO = False


def _obter_embedder_local():
    """Carrega DefaultEmbeddingFunction (ONNX 384d) sob demanda sem depender de rede."""
    global _EMBEDDER_LOCAL, _EMBEDDER_INICIALIZADO
    if _EMBEDDER_INICIALIZADO:
        return _EMBEDDER_LOCAL
    _EMBEDDER_INICIALIZADO = True
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("ENVIRONMENT", "").strip().lower() in ("test", "testing"):
        # Em testes unitários evita download/carga pesada ONNX se não estiver em cache
        _EMBEDDER_LOCAL = None
        return None
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        _EMBEDDER_LOCAL = DefaultEmbeddingFunction()
    except Exception:
        _EMBEDDER_LOCAL = None
    return _EMBEDDER_LOCAL


def _embedding_hash_384(texto: str) -> list[float]:
    """Fallback determinístico de 384 dimensões via n-gramas + SHA-256 (norma L2 = 1)."""
    vec = [0.0] * 384
    tokens = re.findall(r"\b\w+\b", (texto or "").lower())
    if not tokens:
        vec[0] = 1.0
        return vec
    features = list(tokens) + [
        f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)
    ]
    for feat in features:
        digest = hashlib.sha256(feat.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % 384
        sinal = 1.0 if (digest[2] & 1) == 0 else -1.0
        peso = 1.0 + (digest[3] / 255.0) * 0.5
        vec[idx] += sinal * peso
    norma = math.sqrt(sum(x * x for x in vec))
    if norma <= 1e-12:
        vec[0] = 1.0
        return vec
    return [round(x / norma, 6) for x in vec]


def gerar_embedding_local(texto: str) -> list[float]:
    """Gera vetor normalizado de 384 dimensões localmente (0 chamadas à API Gemini)."""
    texto_limpo, _ = sanitizar_dados_sensiveis((texto or "").strip())
    if not texto_limpo:
        return _embedding_hash_384("")
    embedder = _obter_embedder_local()
    if embedder is not None:
        try:
            vetores = embedder([texto_limpo])
            if vetores and len(vetores[0]) == 384:
                vec = [float(x) for x in vetores[0]]
                norma = math.sqrt(sum(x * x for x in vec))
                if norma > 1e-12:
                    return [x / norma for x in vec]
        except Exception:
            pass
    return _embedding_hash_384(texto_limpo)


def dividir_texto_pei_em_chunks(
    texto_pei: str,
    disciplina: str,
    nome_aluno: str,
    tamanho_max: int = 750,
) -> list[str]:
    """Quebra um PEI completo em chunks semânticos higienizados com prefixo de disciplina e aluno."""
    limpo, _ = sanitizar_dados_sensiveis(texto_pei or "")
    if not limpo.strip():
        return []
    disc = (disciplina or "Geral").strip()
    aluno = (nome_aluno or "Estudante").strip()
    prefixo = f"[PEI de {disc} | {aluno}]"

    paragrafos = [p.strip() for p in re.split(r"\n{2,}", limpo) if p.strip()]
    chunks: list[str] = []
    atual = ""
    for par in paragrafos:
        if len(atual) + len(par) + 1 <= tamanho_max:
            atual = f"{atual}\n{par}".strip() if atual else par
        else:
            if atual:
                chunks.append(f"{prefixo} {atual}")
            if len(par) <= tamanho_max:
                atual = par
            else:
                for i in range(0, len(par), tamanho_max):
                    parte = par[i : i + tamanho_max].strip()
                    if parte:
                        chunks.append(f"{prefixo} {parte}")
                atual = ""
    if atual:
        chunks.append(f"{prefixo} {atual}")
    return chunks


async def indexar_contexto_aluno_pgvector(
    id_aluno: int,
    origem: str,
    textos: list[str],
    disciplina: Optional[str] = None,
    pei_id: Optional[str] = None,
    conversa_id: Optional[str] = None,
    substituir_origem_disciplina: bool = False,
    token: Optional[str] = None,
) -> bool:
    """Sanitiza, vetoriza localmente e envia chunks do aluno para o pgvector na API-DB."""
    itens: list[dict[str, Any]] = []
    for raw in textos:
        txt_seguro, _ = sanitizar_dados_sensiveis((raw or "").strip())
        if not txt_seguro:
            continue
        emb = await asyncio.to_thread(gerar_embedding_local, txt_seguro)
        itens.append({"texto": txt_seguro[:5500], "embedding": emb})

    if not itens and not substituir_origem_disciplina:
        return False

    try:
        await salvar_vetores_aluno_db(
            id_aluno=id_aluno,
            origem=origem,
            itens=itens,
            disciplina=disciplina,
            pei_id=pei_id,
            conversa_id=conversa_id,
            substituir_origem_disciplina=substituir_origem_disciplina,
            token=token,
        )
        return True
    except Exception:
        return False


async def buscar_contexto_vetorial_aluno(
    id_aluno: int,
    query: str,
    top_k: int = 6,
    disciplina: Optional[str] = None,
    token: Optional[str] = None,
) -> dict[str, Any]:
    """Gera embedding da consulta e recupera os trechos mais relevantes do aluno no pgvector."""
    emb = await asyncio.to_thread(gerar_embedding_local, query or "")
    try:
        return await buscar_vetores_aluno_db(
            id_aluno=id_aluno,
            embedding=emb,
            top_k=top_k,
            disciplina=disciplina,
            token=token,
        )
    except Exception:
        return {"id_aluno": id_aluno, "trechos": [], "disciplinas_pei": []}
