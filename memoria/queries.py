"""Módulo de consultas semânticas aos documentos oficiais no ChromaDB.

O ChromaDB é utilizado exclusivamente para recuperar trechos destilados dos
PDFs científicos e legais de referência (DUA, Decreto 12.686, WWC).
"""

from __future__ import annotations

from typing import Any, Optional
from memoria.chroma_client import obter_colecao_documentos


def buscar_documentos_referencia(
    query: str,
    n_results: int = 4,
    persist_dir: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Busca trechos dos documentos oficiais e científicos (DUA, Decreto 12.686, WWC).

    Retorna lista de dicionários contendo o texto, título, autor, citação padrão e página.
    """
    if not query or not query.strip():
        return []

    from guardrails import sanitizar_dados_sensiveis

    query_limpa, _ = sanitizar_dados_sensiveis(query)

    colecao = obter_colecao_documentos(persist_dir)
    total = colecao.count()
    if total == 0:
        return []

    limite = min(n_results, total)
    try:
        resultado = colecao.query(
            query_texts=[query_limpa],
            n_results=limite,
        )
    except Exception:
        return []

    documentos = resultado.get("documents", [[]])[0]
    metadados = resultado.get("metadatas", [[]])[0]

    trechos_recuperados: list[dict[str, Any]] = []
    for i in range(len(documentos)):
        meta = metadados[i] if i < len(metadados) else {}
        trechos_recuperados.append(
            {
                "texto": documentos[i],
                "source": meta.get("source", ""),
                "titulo": meta.get("titulo", "Documento de Referência"),
                "autor": meta.get("autor", "MetamorphIA"),
                "categoria": meta.get("categoria", "Pedagogia Inclusiva"),
                "citacao_padrao": meta.get("citacao_padrao", meta.get("source", "")),
                "pagina": meta.get("pagina", 1),
                "similarity": float(meta.get("similarity", 0.0)),
            }
        )

    return trechos_recuperados
