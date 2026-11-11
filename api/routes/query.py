from __future__ import annotations

import json
import re
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth_jwt import verificar_token_jwt
from llm_client import gerar_resposta
from memoria.queries import buscar_documentos_referencia
from api.schemas.response import AlunoResponse

Queryrouter = APIRouter(prefix="/query", tags=["query"])


class AdaptarAtividadeRequest(BaseModel):
    atividade: str = Field(..., description="Descrição ou enunciado da atividade original a ser adaptada")
    neurodivergencia: str = Field(..., description="Condição do estudante (ex: TEA, TDAH, Dislexia, TOD)")
    ano_escolar: Optional[str] = Field(None, description="Série ou ano escolar do aluno (ex: 5º ano fundamental)")
    objetivo_pedagogico: Optional[str] = Field(None, description="Objetivo principal da aprendizagem")


class AdaptarAtividadeResponse(BaseModel):
    atividade_original: str
    neurodivergencia: str
    adaptacoes_sugeridas: str
    recursos_recomendados: list[str]


class PerguntaRequest(BaseModel):
    pergunta: str = Field(..., description="Dúvida pedagógica ou educacional inclusiva")


@Queryrouter.post("/adaptar-atividade", response_model=AdaptarAtividadeResponse)
async def adaptar_atividade_endpoint(
    payload: AdaptarAtividadeRequest,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AdaptarAtividadeResponse:
    from guardrails import sanitizar_dados_sensiveis

    atividade_limpa, _ = sanitizar_dados_sensiveis(payload.atividade)
    neurodiv_limpa, _ = sanitizar_dados_sensiveis(payload.neurodivergencia)

    termo_busca = f"adaptação atividade {neurodiv_limpa} {atividade_limpa[:80]}"
    fontes = buscar_documentos_referencia(termo_busca, n_results=3)

    prompt = f"""
Você é um especialista em Desenho Universal para a Aprendizagem (DUA 3.0) e Educação Inclusiva.
Adapte a seguinte atividade para um estudante neurodivergente:
- Condição / Diagnóstico: {neurodiv_limpa}
- Ano / Faixa Escolar: {payload.ano_escolar or 'Não especificado'}
- Objetivo Pedagógico: {payload.objetivo_pedagogico or 'Garantir a compreensão e participação ativa'}

Atividade Original do Professor:
\"\"\"{atividade_limpa}\"\"\"

ESTRUTURE SUA RESPOSTA EM:
1. ANÁLISE DE BARREIRAS POTENCIAIS NA ATIVIDADE ORIGINAL
2. PROPOSTA DE ATIVIDADE ADAPTADA (Passo a passo com linguagem clara)
3. PRINCÍPIOS DUA APLICADOS (Múltiplos meios de Engajamento, Representação e Expressão)
4. RECURSOS COMPLEMENTARES E ESTRATÉGIAS DE MEDIAÇÃO
"""

    resposta_texto = gerar_resposta(
        prompt=prompt,
        fontes_referencia=fontes,
        formato_referencias="discreto",
    )

    recursos = [
        "Suporte visual / Cartões de rotina",
        "Tempo estendido e pausas sensoriais",
        "Instruções fracionadas em etapas curtas",
        "Opção de resposta oral ou por desenho",
    ]

    return AdaptarAtividadeResponse(
        atividade_original=atividade_limpa,
        neurodivergencia=neurodiv_limpa,
        adaptacoes_sugeridas=resposta_texto,
        recursos_recomendados=recursos,
    )


@Queryrouter.post("/perguntar")
async def perguntar_ia_post(
    payload: PerguntaRequest,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, str]:
    from guardrails import sanitizar_dados_sensiveis

    pergunta_limpa, _ = sanitizar_dados_sensiveis(payload.pergunta)
    fontes = buscar_documentos_referencia(pergunta_limpa, n_results=3)
    resposta = gerar_resposta(pergunta_limpa, fontes_referencia=fontes, formato_referencias="discreto")
    return {"resposta": resposta}


@Queryrouter.get("/enviaTxtIA/{pergunta}")
async def enviaTxtIA(
    pergunta: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, str]:
    from guardrails import sanitizar_dados_sensiveis

    pergunta_limpa, _ = sanitizar_dados_sensiveis(pergunta)
    fontes = buscar_documentos_referencia(pergunta_limpa, n_results=2)
    return {"resposta": gerar_resposta(pergunta_limpa, fontes_referencia=fontes)}


@Queryrouter.get("/Envia/{DescAl}", response_model=AlunoResponse)
async def enviaInfoAluno(
    DescAl: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AlunoResponse:
    prompt = f"""
Gere um JSON com as seguintes informações de um aluno. Retorne APENAS o JSON, sem explicações, sem texto adicional, sem blocos de código markdown.

Campos obrigatórios (use string vazia "" se não souber ou não houver informação):
- "nome": nome completo do aluno
- "idade": idade do aluno (número inteiro, ou null se não souber)
- "serie": série/ano escolar do aluno
- "neurodivergencia": diagnóstico de neurodivergência do aluno (ex: TDAH, TEA, Dislexia)
- "diagnosticado": true se tiver diagnóstico confirmado, false se for suspeita do professor, ou null se não houver nenhuma indicação
- "descricao": descrição geral do aluno com foco em comportamento e aprendizado, sem repetir a neurodivergência já informada no campo anterior

Informações disponíveis:
{DescAl}

Exemplo de saída esperada:
{{"nome":"João Silva","idade":10,"serie":"5º ano","neurodivergencia":"TDAH","diagnosticado":true,"descricao":"Aluno comunicativo..."}}
"""
    result = gerar_resposta(prompt, aplicar_guardrails=False)

    match = re.search(r"\{.*\}", result, re.DOTALL)
    json_str = match.group(0) if match else result

    try:
        dados = json.loads(json_str)
        return AlunoResponse.model_validate(dados)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Não foi possível estruturar os dados do aluno via IA: {exc}",
        )
