from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status

from auth_jwt import verificar_token_jwt
from db_client import ResourceNotFoundError, atualizar_descricao_aluno, obter_aluno, obter_turma
from diagnostico_utils import classificar_diagnostico
from llm_client import gerar_resposta
from memoria.queries import (
    buscar_documentos_referencia,
    calcular_metricas_aluno,
    listar_conversas_aluno,
    verificar_pei_existente,
)
from schemas import AlunoDB, AlunoDashboardResponse, ConversaResumo, PerfilGerarResponse

INTERVALO_INTERACOES_CONTEXTO = 5

router = APIRouter(tags=["dashboard-aluno"])


@router.get("/alunos/{id_aluno}/dashboard", response_model=AlunoDashboardResponse)
async def get_aluno_dashboard(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AlunoDashboardResponse:
    try:
        aluno = await obter_aluno(id_aluno)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aluno {id_aluno} não encontrado.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha de comunicação ao buscar aluno: {exc}",
        )

    nome_turma = "Sem turma atribuída"
    if aluno.id_turma:
        try:
            turma = await obter_turma(aluno.id_turma)
            nome_turma = turma.nome_turma or f"Turma {aluno.id_turma}"
        except Exception:
            nome_turma = f"Turma {aluno.id_turma}"

    diagnostico_limpo, status_diag = classificar_diagnostico(aluno.neurodiv_aluno)
    perfil_ia = aluno.perfil_ia or aluno.desc_aluno
    pei_gerado = verificar_pei_existente(id_aluno)
    metricas = calcular_metricas_aluno(id_aluno)

    tipo_usuario = usuario_autenticado.get("tipo_usuario", "normal")
    pode_chat = usuario_autenticado.get("pode_chat", True)
    if tipo_usuario == "adm_institucional" or pode_chat is False:
        conversas = []
    else:
        id_prof_token = usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub")
        id_prof_filtro = int(id_prof_token) if id_prof_token is not None else None
        conversas = listar_conversas_aluno(id_aluno, id_prof=id_prof_filtro)

    return AlunoDashboardResponse(
        id_aluno=aluno.id_aluno,
        nome=aluno.nome_aluno,
        idade=aluno.idade_aluno,
        turma=nome_turma,
        diagnostico=diagnostico_limpo,
        status_diagnostico=status_diag,
        perfil_ia=perfil_ia,
        pei_gerado=pei_gerado,
        atividades_adaptadas=metricas["atividades_adaptadas"],
        engajamento_percent=metricas["engajamento_percent"],
        desempenho_por_area=metricas["desempenho_por_area"],
        historico_conversas=[ConversaResumo(**c) for c in conversas],
    )


async def gerar_contexto_sintetizado_aluno(
    aluno: AlunoDB,
    historico_conversas: str = "",
) -> tuple[str, bool]:
    from guardrails import sanitizar_dados_sensiveis

    id_aluno = aluno.id_aluno
    fontes = buscar_documentos_referencia(
        "perfil pedagógico neurodivergência potencialidades barreiras adaptação",
        n_results=2,
    )

    nome_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    perfil_atual_bruto = aluno.perfil_ia or aluno.desc_aluno or ""
    perfil_atual_limpo, _ = sanitizar_dados_sensiveis(perfil_atual_bruto)
    historico_limpo, _ = sanitizar_dados_sensiveis(historico_conversas or "")

    prompt = f"""
Com base nas conversas pedagógicas com a IA e nos dados atuais do estudante, gere um texto consolidado, rico e prático descrevendo como o aluno é (este texto será salvo como o Perfil/Contexto oficial do aluno para a IA):
- Aluno: {nome_limpo} (Idade: {aluno.idade_aluno or 'Não informada'})
- Neurodivergência / Condição: {aluno.neurodiv_aluno}
- Perfil / Contexto anterior do aluno: {perfil_atual_limpo or 'Nenhum perfil anterior'}
- Conversas recentes sobre o aluno:
{historico_limpo or 'Sem conversas recentes registradas'}

Estruture o texto abordando tudo o que for relevante sobre o aluno:
1. Neurodivergência e Como o Aluno É (características cognitivas, sensoriais, comportamentais e interesses/hiperfocos)
2. O que Precisa de Adaptação (principais barreiras, gatilhos de distração/sobrecarga e adaptações necessárias)
3. O que Faz Rápido e Facilidades (tarefas que realiza com agilidade, autonomia e pontos fortes)
4. Qualidade, Ritmo e Engajamento (qualidade das entregas, atenção a detalhes, formatos em que rende melhor e evolução observada)
"""

    perfil_texto = gerar_resposta(
        prompt=prompt,
        fontes_referencia=fontes,
    )

    sincronizado = True
    try:
        await atualizar_descricao_aluno(id_aluno, perfil_texto)
    except Exception:
        sincronizado = False

    return perfil_texto, sincronizado


async def verificar_e_atualizar_contexto_aluno(
    aluno: AlunoDB,
    historico_conversas: str,
    total_interacoes: int,
    limite_interacoes: int = INTERVALO_INTERACOES_CONTEXTO,
) -> Optional[str]:
    if total_interacoes < limite_interacoes or (total_interacoes % limite_interacoes) != 0:
        return None

    perfil_texto, _ = await gerar_contexto_sintetizado_aluno(
        aluno=aluno,
        historico_conversas=historico_conversas,
    )
    return perfil_texto


@router.post("/alunos/{id_aluno}/perfil", response_model=PerfilGerarResponse)
async def gerar_e_atualizar_perfil(
    id_aluno: int,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PerfilGerarResponse:
    try:
        aluno = await obter_aluno(id_aluno)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aluno {id_aluno} não encontrado.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha de comunicação ao buscar aluno: {exc}",
        )

    from api.routes.chat import obter_historico_sessao_aluno

    historico_str = obter_historico_sessao_aluno(id_aluno)
    perfil_texto, sincronizado = await gerar_contexto_sintetizado_aluno(
        aluno=aluno,
        historico_conversas=historico_str,
    )

    return PerfilGerarResponse(
        id_aluno=id_aluno,
        perfil_gerado=perfil_texto,
        sincronizado_api_db=sincronizado,
    )
