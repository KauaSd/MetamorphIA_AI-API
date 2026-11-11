"""Chain RAG: recupera contexto pedagógico no ChromaDB e gera resposta fundamentada."""

from __future__ import annotations

from llm_client import gerar_resposta
from src.retriever import buscar_trechos


def _montar_contexto(pergunta: str) -> tuple[str, list[dict]]:
    trechos = buscar_trechos(pergunta)
    if not trechos:
        return "Nenhum trecho específico foi recuperado da base documental.", []

    partes = []
    fontes = []
    for indice, t in enumerate(trechos, start=1):
        titulo = t.metadata.get("titulo", t.metadata.get("source", "Fonte Oficial"))
        partes.append(f"[Trecho {indice} | {titulo}]\n{t.texto}")
        fontes.append(t.metadata)

    return "\n\n".join(partes), fontes


def resposta(pergunta: str) -> str:
    """Responde à dúvida pedagógica utilizando os documentos semelhantes recuperados."""
    contexto, fontes = _montar_contexto(pergunta)
    return gerar_resposta(
        prompt=pergunta,
        contexto_rag=contexto,
        fontes_referencia=fontes,
        formato_referencias="discreto",
    )


if __name__ == "__main__":
    pergunta_usuario = input("Pergunta pedagógica para a IA: ")
    print(resposta(pergunta_usuario))