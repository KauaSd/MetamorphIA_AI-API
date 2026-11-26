from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import re
from typing import Any, Optional
import unicodedata
from fastapi import APIRouter, Depends, HTTPException, status

from auth_jwt import validar_permissao_chat_usuario, verificar_token_jwt
from config import get_settings
from db_client import (
    atualizar_descricao_aluno,
    levantar_http_de_erro_db,
    listar_conversas_aluno_db,
    obter_aluno,
    obter_alunos_por_turma,
    obter_mensagens_conversa_db,
    obter_turma,
)
from diagnostico_utils import classificar_diagnostico
from llm_client import gerar_resposta_async
from memoria.queries import buscar_documentos_referencia
from schemas import (
    AlunoDB,
    AlunoDashboardResponse,
    AlunoDescricaoBreveResponse,
    ConversaResumo,
    DisciplinaItem,
    IndicadorDesenvolvimentoItem,
    MetaBimestreItem,
    PeiDisciplinaItem,
    PeisPorDisciplinaResumo,
    PerfilGerarRequest,
    PerfilGerarResponse,
    TurmaDashboardResponse,
)

router = APIRouter(tags=["dashboard-aluno"])

CATALOGO_DISCIPLINAS: list[dict[str, str]] = [
    {"id": "mat", "nome": "Matemática"},
    {"id": "por", "nome": "L. Portuguesa"},
    {"id": "cie", "nome": "Ciências"},
    {"id": "ing", "nome": "L. Inglesa"},
    {"id": "his", "nome": "História"},
    {"id": "geo", "nome": "Geografia"},
    {"id": "art", "nome": "Arte"},
    {"id": "edf", "nome": "Educação Física"},
    {"id": "fil", "nome": "Filosofia"},
    {"id": "soc", "nome": "Sociologia"},
    {"id": "bio", "nome": "Biologia"},
    {"id": "fis", "nome": "Física"},
    {"id": "qui", "nome": "Química"},
    {"id": "ens", "nome": "Ensino Religioso"},
]

_ALIASES_DISCIPLINA: dict[str, str] = {
    "lingua portuguesa": "l. portuguesa",
    "l portuguesa": "l. portuguesa",
    "portugues": "l. portuguesa",
    "lingua inglesa": "l. inglesa",
    "l inglesa": "l. inglesa",
    "ingles": "l. inglesa",
    "artes": "arte",
    "ed fisica": "educacao fisica",
    "ed. fisica": "educacao fisica",
}

_INDICADORES_BASE: list[dict[str, Any]] = [
    {
        "id": "autonomia",
        "titulo": "Autonomia nas Atividades",
        "escala_esquerda": "Precisa de apoio",
        "escala_centro": "Com pistas",
        "escala_direita": "Faz sozinho",
        "nivel": 3,
        "cor": "warning",
    },
    {
        "id": "foco",
        "titulo": "Foco e Atenção",
        "escala_esquerda": "Disperso",
        "escala_centro": "Oscila",
        "escala_direita": "Sustentado",
        "nivel": 2,
        "cor": "accent",
    },
    {
        "id": "regulacao",
        "titulo": "Regulação Emocional",
        "escala_esquerda": "Sensível",
        "escala_centro": "Em adaptação",
        "escala_direita": "Estável",
        "nivel": 3,
        "cor": "info",
    },
    {
        "id": "engajamento",
        "titulo": "Engajamento com Adaptações",
        "escala_esquerda": "Baixo",
        "escala_centro": "Moderado",
        "escala_direita": "Alto",
        "nivel": 3,
        "cor": "success",
    },
]


def _normalizar_chave_disciplina(nome: str) -> str:
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFKD", (nome or "").strip().lower())
        if not unicodedata.combining(c)
    )
    limpo = re.sub(r"\s+", " ", sem_acento).strip()
    return _ALIASES_DISCIPLINA.get(limpo, limpo)


_MAPA_CATALOGO_POR_CHAVE: dict[str, dict[str, str]] = {
    _normalizar_chave_disciplina(item["nome"]): item for item in CATALOGO_DISCIPLINAS
}


def _slug_disciplina(nome: str) -> str:
    item = _MAPA_CATALOGO_POR_CHAVE.get(_normalizar_chave_disciplina(nome))
    if item:
        return item["id"]
    chave = re.sub(r"[^a-z0-9]+", "_", _normalizar_chave_disciplina(nome)).strip("_")
    return chave[:12] or "disc"


@router.get("/disciplinas", response_model=list[DisciplinaItem])
async def listar_disciplinas() -> list[DisciplinaItem]:
    """Lista todas as disciplinas disponíveis para seleção no perfil do professor."""
    return [DisciplinaItem(**d) for d in CATALOGO_DISCIPLINAS]

_TERMOS_OPERACIONAIS_DESCARTADOS = {
    "oi",
    "ola",
    "olá",
    "bom dia",
    "boa tarde",
    "boa noite",
    "ok",
    "okay",
    "certo",
    "entendi",
    "obrigado",
    "obrigada",
    "valeu",
    "sim",
    "não",
    "nao",
    "1",
    "2",
    "3",
    "4",
    "opção 1",
    "opcao 1",
    "opção 2",
    "opcao 2",
    "opção 3",
    "opcao 3",
    "documento",
    "gerar documento",
    "baixar pei",
    "enviar documento",
    "em pdf",
    "em docx",
    "formato pdf",
    "formato docx",
}

_INDICADORES_FEEDBACK_ALUNO = (
    "funcionou",
    "não funcionou",
    "nao funcionou",
    "conseguiu",
    "não conseguiu",
    "nao conseguiu",
    "travou",
    "dificuldade",
    "facilidade",
    "gostou",
    "adorou",
    "rejeitou",
    "recusou",
    "agitado",
    "ansioso",
    "calmo",
    "focou",
    "distraiu",
    "fez rápido",
    "fez rapido",
    "demorou",
    "avançou",
    "avancou",
    "melhorou",
    "evoluiu",
    "na aula hoje",
    "na aula de hoje",
    "hoje ele",
    "hoje ela",
    "testei",
    "apliquei",
    "percebi",
    "notei",
    "observ",
    "feedback",
    "resultado",
    "reagiu",
)


def deve_atualizar_memoria_conversa(
    mensagem_usuario: str,
    resposta_ia: str,
    gerou_atividade_adaptada: bool = False,
) -> bool:
    """Pré-filtro determinístico (Abordagem 1): ignora interações curtas/operacionais e aprova
    feedbacks sobre o aluno, atividades adaptadas concluídas ou trocas pedagógicas densas.
    """
    msg = (mensagem_usuario or "").strip()
    msg_lower = msg.lower().strip(".!?,;: ")
    if not msg_lower or msg_lower in _TERMOS_OPERACIONAIS_DESCARTADOS:
        return False

    if len(msg) >= 15 and any(ind in msg_lower for ind in _INDICADORES_FEEDBACK_ALUNO):
        return True

    if gerou_atividade_adaptada and len((resposta_ia or "").strip()) >= 150:
        return True

    if len(msg) < 80 or len((resposta_ia or "").strip()) < 250:
        return False

    return True


def _limpar_markdown_perfil(texto: str) -> str:
    """Remove marcações Markdown (negrito, títulos, separadores) do perfil do aluno."""
    if not texto:
        return ""
    linhas: list[str] = []
    for ln in str(texto).splitlines():
        s = ln.strip()
        if not s:
            if linhas and linhas[-1] != "":
                linhas.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}", s):
            continue
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
        s = re.sub(r"__(.*?)__", r"\1", s)
        s = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)", r"\1", s)
        s = re.sub(r"^\s*[-*]\s+", "• ", s)
        linhas.append(s)
    return "\n".join(linhas).strip()


def _montar_descricao_breve(
    nome_aluno: str,
    diagnostico: str,
    perfil_ia: Optional[str],
    desc_aluno: Optional[str],
) -> str:
    """Extrai uma descrição breve (1-2 frases, até ~220 caracteres) para o card Perfil do Aluno."""
    bruto = _limpar_markdown_perfil((perfil_ia or desc_aluno or "").strip())
    if not bruto:
        return f"{nome_aluno} possui perfil {diagnostico} e está em acompanhamento pedagógico inclusivo."
    linhas = []
    for ln in bruto.splitlines():
        limpa = re.sub(
            r"^\s*\d+\.\s*(?:Neurodiverg[eê]ncia[^:]*|Estrat[eé]gias[^:]*|Pontos\s+Fortes[^:]*|O\s+que\s+Faz[^:]*|Hist[oó]rico[^:]*)\s*:\s*",
            "",
            ln.strip(),
            flags=re.I,
        ).strip()
        if limpa:
            linhas.append(limpa)
    texto = " ".join(linhas) if linhas else bruto
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= 220:
        return texto
    corte = texto[:217]
    ultimo_ponto = max(corte.rfind("."), corte.rfind("!"), corte.rfind("?"))
    if ultimo_ponto >= 60:
        return corte[: ultimo_ponto + 1].strip()
    ultimo_espaco = corte.rfind(" ")
    if ultimo_espaco >= 60:
        return corte[:ultimo_espaco].rstrip(",;:") + "..."
    return corte + "..."


@router.get("/alunos/{id_aluno}/descricao-breve", response_model=AlunoDescricaoBreveResponse)
async def get_aluno_descricao_breve(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AlunoDescricaoBreveResponse:
    """Retorna uma descrição breve do aluno para exibição no card Perfil do Aluno."""
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")

    nome_turma = "Sem turma atribuída"
    if aluno.id_turma:
        try:
            turma = await obter_turma(aluno.id_turma, token=token_jwt)
            nome_turma = turma.nome_turma or f"Turma {aluno.id_turma}"
        except Exception:
            nome_turma = f"Turma {aluno.id_turma}"

    diagnostico_limpo, _ = classificar_diagnostico(aluno.neurodiv_aluno)
    tipo_usuario = usuario_autenticado.get("tipo_usuario", "normal")
    pode_chat = usuario_autenticado.get("pode_chat", False)
    if (tipo_usuario == "adm_institucional" or pode_chat is not True) and tipo_usuario != "adm_sistema":
        desc_breve = f"{aluno.nome_aluno} possui perfil {diagnostico_limpo}."
    else:
        desc_breve = _montar_descricao_breve(
            aluno.nome_aluno, diagnostico_limpo, aluno.perfil_ia, aluno.desc_aluno
        )

    return AlunoDescricaoBreveResponse(
        id_aluno=aluno.id_aluno,
        nome=aluno.nome_aluno,
        idade=aluno.idade_aluno,
        neurodivergencia=diagnostico_limpo,
        turma=nome_turma,
        descricao_breve=desc_breve,
    )


@router.get("/alunos/{id_aluno}/dashboard", response_model=AlunoDashboardResponse)
async def get_aluno_dashboard(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AlunoDashboardResponse:
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")

    nome_turma = "Sem turma atribuída"
    if aluno.id_turma:
        try:
            turma = await obter_turma(aluno.id_turma, token=token_jwt)
            nome_turma = turma.nome_turma or f"Turma {aluno.id_turma}"
        except Exception:
            nome_turma = f"Turma {aluno.id_turma}"

    diagnostico_limpo, status_diag = classificar_diagnostico(aluno.neurodiv_aluno)
    perfil_bruto = aluno.perfil_ia or aluno.desc_aluno
    perfil_ia = _limpar_markdown_perfil(perfil_bruto) if perfil_bruto else None
    pei_gerado = bool(aluno.pei_ativo)

    conversas: list[dict[str, Any]] = []
    atividades_adaptadas = 0
    engajamento_percent = 65 if pei_gerado else 50
    try:
        dados_conv = await listar_conversas_aluno_db(
            id_aluno, incluir_metricas=True, token=token_jwt
        )
        if isinstance(dados_conv, dict):
            conversas = dados_conv.get("conversas", [])
            atividades_adaptadas = int(dados_conv.get("atividades_adaptadas", 0))
            engajamento_percent = int(dados_conv.get("engajamento_percent", engajamento_percent))
    except Exception:
        pass

    tipo_usuario = usuario_autenticado.get("tipo_usuario", "normal")
    pode_chat = usuario_autenticado.get("pode_chat", False)
    if (tipo_usuario == "adm_institucional" or pode_chat is not True) and tipo_usuario != "adm_sistema":
        conversas = []
        perfil_ia = None

    # Monta PEIs por disciplina com base nas tags (disciplinas) do professor autenticado
    tags_prof = [str(t).strip() for t in (usuario_autenticado.get("tags") or []) if str(t).strip()]
    status_por_chave: dict[str, str] = {}
    datas_concluidos: list[datetime] = []
    for reg in aluno.peis_disciplinas or []:
        disc_nome = str(reg.get("disciplina") or "").strip()
        if not disc_nome:
            continue
        chave = _normalizar_chave_disciplina(disc_nome)
        st = reg.get("status")
        if st in ("concluido", "em_andamento", "nao_iniciado"):
            status_por_chave[chave] = st
        if st == "concluido" and reg.get("atualizado_em"):
            try:
                datas_concluidos.append(
                    datetime.fromisoformat(str(reg["atualizado_em"]).replace("Z", "+00:00"))
                )
            except Exception:
                pass

    if not tags_prof and aluno.peis_disciplinas:
        tags_prof = [
            str(r.get("disciplina")).strip()
            for r in aluno.peis_disciplinas
            if r.get("disciplina")
        ]

    disciplinas_items: list[PeiDisciplinaItem] = []
    for nome_disc in tags_prof:
        chave = _normalizar_chave_disciplina(nome_disc)
        st_disc = status_por_chave.get(chave, "nao_iniciado")
        disciplinas_items.append(
            PeiDisciplinaItem(
                id=_slug_disciplina(nome_disc),
                disciplina=nome_disc,
                status=st_disc,  # type: ignore[arg-type]
            )
        )

    total_disc = len(disciplinas_items)
    concluidos_disc = sum(1 for d in disciplinas_items if d.status == "concluido")
    em_andamento_disc = sum(1 for d in disciplinas_items if d.status == "em_andamento")
    if total_disc > 0 and concluidos_disc == total_disc:
        status_geral = "CONCLUIDO"
    elif concluidos_disc > 0 or em_andamento_disc > 0 or pei_gerado:
        status_geral = "EM ANDAMENTO"
    else:
        status_geral = "NAO_INICIADO"

    ultimo_envio_str: Optional[str] = None
    proximo_periodo_str: Optional[str] = None
    prazo_expirado = bool(aluno.pei_feito and not pei_gerado)
    if datas_concluidos:
        ultimo_dt = max(datas_concluidos)
        ultimo_envio_str = ultimo_dt.strftime("%d/%m/%y")
    if aluno.pei_valido_ate:
        try:
            dt_val = datetime.fromisoformat(aluno.pei_valido_ate.replace("Z", "+00:00"))
            if dt_val.tzinfo is None:
                from datetime import timezone as _tz
                dt_val = dt_val.replace(tzinfo=_tz.utc)
            if dt_val <= datetime.now(dt_val.tzinfo):
                prazo_expirado = True
            if not ultimo_envio_str and aluno.pei_feito:
                ultimo_envio_str = (dt_val - timedelta(days=60)).strftime("%d/%m/%y")
            fim_janela = dt_val + timedelta(days=11)
            proximo_periodo_str = f"{dt_val.strftime('%d/%m')} até {fim_janela.strftime('%d/%m/%y')}"
        except Exception:
            pass

    atrasado = prazo_expirado and (total_disc == 0 or concluidos_disc < total_disc)
    if atrasado and status_geral != "CONCLUIDO":
        status_geral = "ATRASADO"

    peis_por_disciplina = PeisPorDisciplinaResumo(
        status_geral=status_geral,
        concluidos=concluidos_disc,
        total=total_disc,
        atrasado=atrasado,
        prazo_expirado=prazo_expirado,
        ultimo_envio=ultimo_envio_str,
        proximo_periodo=proximo_periodo_str,
        disciplinas=disciplinas_items,
    )

    indicadores = _normalizar_indicadores(aluno.indicadores_desenvolvimento)
    metas = _normalizar_metas(aluno.metas_bimestre, aluno.neurodiv_aluno, pei_gerado)

    return AlunoDashboardResponse(
        id_aluno=aluno.id_aluno,
        nome=aluno.nome_aluno,
        idade=aluno.idade_aluno,
        turma=nome_turma,
        diagnostico=diagnostico_limpo,
        status_diagnostico=status_diag,
        desc_aluno=aluno.desc_aluno,
        perfil_ia=perfil_ia,
        descricao_breve=_montar_descricao_breve(
            aluno.nome_aluno, diagnostico_limpo, perfil_ia, aluno.desc_aluno
        )
        if perfil_ia is not None
        else None,
        pei_gerado=pei_gerado,
        atividades_adaptadas=atividades_adaptadas,
        engajamento_percent=engajamento_percent,
        desempenho_por_area={
            "matematica": 78,
            "leitura": 64,
            "escrita": 70,
            "ciencias": 72,
        },
        historico_conversas=[ConversaResumo(**c) for c in conversas],
        peis_por_disciplina=peis_por_disciplina,
        metas_bimestre=metas,
        indicadores_desenvolvimento=indicadores,
    )


def _normalizar_indicadores(
    brutos: Optional[list[dict[str, Any]]],
) -> list[IndicadorDesenvolvimentoItem]:
    mapa_bruto = {
        str(item.get("id") or "").strip().lower(): item
        for item in (brutos or [])
        if isinstance(item, dict)
    }
    saida: list[IndicadorDesenvolvimentoItem] = []
    for base in _INDICADORES_BASE:
        item_raw = mapa_bruto.get(base["id"], {})
        try:
            nivel = int(item_raw.get("nivel", base["nivel"]))
        except Exception:
            nivel = base["nivel"]
        nivel = max(1, min(5, nivel))
        saida.append(
            IndicadorDesenvolvimentoItem(
                id=base["id"],
                titulo=base["titulo"],
                escala_esquerda=base["escala_esquerda"],
                escala_centro=base["escala_centro"],
                escala_direita=base["escala_direita"],
                nivel=nivel,
                cor=base["cor"],
            )
        )
    return saida


def _normalizar_metas(
    brutas: Optional[list[dict[str, Any]]],
    neurodivergencia: str = "",
    pei_gerado: bool = False,
) -> list[MetaBimestreItem]:
    saida: list[MetaBimestreItem] = []
    for idx, item in enumerate(brutas or [], start=1):
        if not isinstance(item, dict):
            continue
        titulo = str(item.get("titulo") or "").strip()
        if not titulo:
            continue
        st = str(item.get("status") or "em_progresso").strip()
        if st not in ("em_progresso", "a_iniciar", "atingida"):
            st = "em_progresso"
        saida.append(
            MetaBimestreItem(
                id=str(item.get("id") or f"m{idx}"),
                titulo=titulo[:140],
                status=st,  # type: ignore[arg-type]
            )
        )
    if saida:
        return saida[:5]
    if pei_gerado:
        return [
            MetaBimestreItem(id="m1", titulo="Consolidar rotina visual nas atividades", status="a_iniciar"),
            MetaBimestreItem(id="m2", titulo="Ampliar autonomia em tarefas curtas", status="a_iniciar"),
            MetaBimestreItem(id="m3", titulo=f"Participar das adaptações ({neurodivergencia or 'DUA'})", status="a_iniciar"),
        ]
    return []


def _avaliar_indicadores_e_metas_deterministico(
    perfil_texto: str,
    historico_conversas: str,
    contexto_pei: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    texto = f"{perfil_texto}\n{historico_conversas}\n{contexto_pei}".lower()
    niveis = {"autonomia": 3, "foco": 2, "regulacao": 3, "engajamento": 3}

    if any(k in texto for k in ("sozinho", "autonomia", "fez rápido", "fez rapido", "conseguiu")):
        niveis["autonomia"] = 4
    elif any(k in texto for k in ("apoio individual", "precisa de apoio", "travou")):
        niveis["autonomia"] = 2

    if any(k in texto for k in ("focou", "sustentado", "manteve foco", "concentrou")):
        niveis["foco"] = 4
    elif any(k in texto for k in ("dispers", "distra", "fragmentad", "oscila")):
        niveis["foco"] = 2

    if any(k in texto for k in ("calmo", "estável", "estavel", "tranquilo")):
        niveis["regulacao"] = 4
    elif any(k in texto for k in ("agitado", "ansioso", "sensível", "sensivel", "sobrecarga")):
        niveis["regulacao"] = 2

    if any(k in texto for k in ("adorou", "gostou", "funcionou", "engaj", "participou", "evoluiu", "melhorou")):
        niveis["engajamento"] = 4
    elif any(k in texto for k in ("rejeitou", "recusou", "não funcionou", "nao funcionou")):
        niveis["engajamento"] = 2

    indicadores = [{**base, "nivel": niveis[base["id"]]} for base in _INDICADORES_BASE]
    sem_conversas = not (historico_conversas or "").strip()
    metas = [
        {
            "id": "m1",
            "titulo": "Manter foco em atividades divididas em blocos curtos",
            "status": "a_iniciar" if sem_conversas else ("atingida" if niveis["foco"] >= 4 else "em_progresso"),
        },
        {
            "id": "m2",
            "titulo": "Realizar tarefas com apoio visual e pistas graduais",
            "status": "a_iniciar" if sem_conversas else ("em_progresso" if niveis["autonomia"] >= 3 else "a_iniciar"),
        },
        {
            "id": "m3",
            "titulo": "Engajar nas propostas adaptadas com regulação emocional",
            "status": "a_iniciar" if sem_conversas else ("atingida" if niveis["engajamento"] >= 4 and niveis["regulacao"] >= 4 else "em_progresso"),
        },
    ]
    return indicadores, metas


async def _gerar_indicadores_e_metas_ia(
    aluno: AlunoDB,
    perfil_texto: str,
    historico_conversas: str = "",
    contexto_pei: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gera indicadores de desenvolvimento (1..5) e metas do bimestre a partir da memória consolidada."""
    cfg = get_settings()
    if (cfg.LLM_PROVIDER or "").strip().lower() == "mock":
        return _avaliar_indicadores_e_metas_deterministico(
            perfil_texto, historico_conversas, contexto_pei
        )

    sem_conversas = not (historico_conversas or "").strip()
    regra_status_metas = (
        '- Como ainda não há conversas de acompanhamento, "status" de TODAS as metas deve ser obrigatoriamente "a_iniciar".'
        if sem_conversas
        else '- "status" das metas deve ser exatamente "em_progresso", "a_iniciar" ou "atingida".'
    )
    prompt = f"""Com base exclusivamente na memória consolidada do aluno, no PEI e nas conversas recentes com o professor, avalie os 4 indicadores de desenvolvimento e as 3 metas do bimestre.
Responda APENAS com um JSON válido, sem markdown e sem explicações extras, no formato exato:
{{
  "indicadores_desenvolvimento": [
    {{"id": "autonomia", "nivel": 3}},
    {{"id": "foco", "nivel": 2}},
    {{"id": "regulacao", "nivel": 4}},
    {{"id": "engajamento", "nivel": 4}}
  ],
  "metas_bimestre": [
    {{"id": "m1", "titulo": "Título curto da meta 1", "status": "a_iniciar"}},
    {{"id": "m2", "titulo": "Título curto da meta 2", "status": "a_iniciar"}},
    {{"id": "m3", "titulo": "Título curto da meta 3", "status": "a_iniciar"}}
  ]
}}
Regras:
- "nivel" deve ser inteiro de 1 a 5 (1=muito baixo/precisa de muito apoio, 3=intermediário/com pistas, 5=alto/autônomo).
{regra_status_metas}
- Neurodivergência: {aluno.neurodiv_aluno}
- Memória consolidada (Perfil IA):
{perfil_texto[:2500]}
- Conversas recentes:
{historico_conversas[:1500] or 'Sem novas conversas'}
"""
    try:
        bruto = await gerar_resposta_async(
            prompt=prompt,
            fontes_referencia=[],
            aplicar_guardrails=False,
        )
        limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", (bruto or "").strip(), flags=re.I)
        match = re.search(r"\{.*\}", limpo, flags=re.S)
        dados = json.loads(match.group(0) if match else limpo)
        inds = [
            item.model_dump()
            for item in _normalizar_indicadores(dados.get("indicadores_desenvolvimento"))
        ]
        metas = [
            item.model_dump()
            for item in _normalizar_metas(
                dados.get("metas_bimestre"), aluno.neurodiv_aluno, True
            )
        ]
        if sem_conversas:
            for m in metas:
                m["status"] = "a_iniciar"
        if inds and metas:
            return inds, metas
    except Exception:
        pass

    return _avaliar_indicadores_e_metas_deterministico(
        perfil_texto, historico_conversas, contexto_pei
    )


async def gerar_contexto_sintetizado_aluno(
    aluno: AlunoDB,
    historico_conversas: str = "",
    contexto_pei: str = "",
    sincronizar_db: bool = True,
    token: Optional[str] = None,
) -> tuple[str, bool, list[dict[str, Any]], list[dict[str, Any]]]:
    """Sintetiza e evolui cumulativamente o perfil_ia, indicadores e metas do aluno na API-DB."""
    from guardrails import sanitizar_dados_sensiveis

    id_aluno = aluno.id_aluno
    fontes = await asyncio.to_thread(
        buscar_documentos_referencia,
        "perfil pedagógico neurodivergência potencialidades barreiras adaptação",
        n_results=2,
    )

    nome_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    perfil_atual_bruto = aluno.perfil_ia or aluno.desc_aluno or ""
    perfil_atual_limpo, _ = sanitizar_dados_sensiveis(perfil_atual_bruto)
    historico_limpo, _ = sanitizar_dados_sensiveis(historico_conversas or "")
    contexto_pei_limpo, _ = sanitizar_dados_sensiveis(contexto_pei or "")

    prompt = f"""
Com base na memória acumulada do estudante, no PEI elaborado e nas novas interações/feedbacks relevantes, gere um texto consolidado, rico e prático descrevendo tudo o que a IA deve lembrar sobre este aluno (este texto será salvo como o Perfil IA oficial do estudante no banco):
- Aluno: {nome_limpo} (Idade: {aluno.idade_aluno or 'Não informada'})
- Neurodivergência / Condição: {aluno.neurodiv_aluno}
- Memória / Perfil IA acumulado anteriormente (PRESERVE aprendizados relevantes anteriores):
{perfil_atual_limpo or 'Nenhum perfil anterior'}
- Planejamento do PEI deste bimestre:
{contexto_pei_limpo[:3000] if contexto_pei_limpo else 'Já contemplado na memória anterior'}
- Novas perguntas, adaptações aplicadas e feedbacks recentes sobre o aluno:
{historico_limpo or 'Sem novas conversas adicionais neste momento'}

Estruture o texto abordando tudo o que for relevante para que a IA lembre em futuras conversas:
1. Neurodivergência e Como o Aluno É (características cognitivas, sensoriais, comportamentais e interesses/hiperfocos)
2. Estratégias do PEI e O que Precisa de Adaptação (barreiras, gatilhos de distração/sobrecarga e recursos que funcionam)
3. O que Faz Rápido, Facilidades e Pontos Fortes
4. Histórico de Respostas, Feedbacks Práticos de Sala de Aula e Evolução Observada

REGRAS DE FORMATAÇÃO: Escreva em texto puro, sem Markdown (NÃO use **negrito**, *itálico*, ### títulos ou --- separadores). Use apenas parágrafos simples ou tópicos com '• '.
"""

    perfil_texto = await gerar_resposta_async(
        prompt=prompt,
        fontes_referencia=fontes,
        aplicar_guardrails=False,
    )
    perfil_texto = _limpar_markdown_perfil(perfil_texto)
    perfil_texto, _ = sanitizar_dados_sensiveis(perfil_texto)

    indicadores, metas = await _gerar_indicadores_e_metas_ia(
        aluno=aluno,
        perfil_texto=perfil_texto,
        historico_conversas=historico_limpo,
        contexto_pei=contexto_pei_limpo,
    )

    sincronizado = False
    if sincronizar_db:
        try:
            await atualizar_descricao_aluno(
                id_aluno,
                perfil_ia=perfil_texto,
                indicadores_desenvolvimento=indicadores,
                metas_bimestre=metas,
                token=token,
            )
            sincronizado = True
        except Exception:
            sincronizado = False

    return perfil_texto, sincronizado, indicadores, metas


@router.post("/alunos/{id_aluno}/perfil", response_model=PerfilGerarResponse)
async def gerar_e_atualizar_perfil(
    id_aluno: int,
    payload: Optional[PerfilGerarRequest] = None,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PerfilGerarResponse:
    validar_permissao_chat_usuario(usuario_autenticado)
    req = payload or PerfilGerarRequest()
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")

    if not aluno.pei_ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="É necessário possuir um PEI válido para gerar ou atualizar o perfil pedagógico do aluno.",
        )

    historico_str = (req.historico_conversas or "").strip()
    if not historico_str:
        try:
            convs = await listar_conversas_aluno_db(id_aluno, incluir_metricas=False, token=token_jwt)
            linhas: list[str] = []
            for c in (convs or [])[:3]:
                cid = c.get("conversa_id")
                if not cid:
                    continue
                msgs = await obter_mensagens_conversa_db(id_aluno, cid, token=token_jwt)
                for m in msgs[-8:]:
                    papel = "Professor" if m.get("role") == "user" else "Assistente"
                    conteudo = (m.get("conteudo") or "").strip()
                    if papel == "Assistente" and len(conteudo) > 300:
                        conteudo = conteudo[:300] + "..."
                    if conteudo:
                        linhas.append(f"{papel}: {conteudo}")
            historico_str = "\n".join(linhas[-20:])
        except Exception:
            historico_str = ""

    perfil_texto, sincronizado, indicadores, metas = await gerar_contexto_sintetizado_aluno(
        aluno=aluno,
        historico_conversas=historico_str,
        sincronizar_db=not req.persistido_pelo_gateway,
        token=token_jwt,
    )
    if req.persistido_pelo_gateway:
        sincronizado = True

    return PerfilGerarResponse(
        id_aluno=id_aluno,
        perfil_gerado=perfil_texto,
        sincronizado_api_db=sincronizado,
        indicadores_desenvolvimento=[IndicadorDesenvolvimentoItem(**i) for i in indicadores],
        metas_bimestre=[MetaBimestreItem(**m) for m in metas],
    )


@router.get("/turmas/{id_turma}/dashboard", response_model=TurmaDashboardResponse)
async def get_turma_dashboard(
    id_turma: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> TurmaDashboardResponse:
    """Retorna métricas e insights consolidados de uma turma a partir da API-DB."""
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        turma = await obter_turma(id_turma, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Turma {id_turma} não encontrada.")

    try:
        alunos_turma = await obter_alunos_por_turma(id_turma, token=token_jwt)
    except Exception:
        alunos_turma = []

    total_alunos = len(alunos_turma)
    peis_ativos = sum(1 for a in alunos_turma if a.pei_ativo)
    adaptacoes_total = 0
    for a in alunos_turma:
        try:
            dados_m = await listar_conversas_aluno_db(
                a.id_aluno, incluir_metricas=True, token=token_jwt
            )
            if isinstance(dados_m, dict):
                adaptacoes_total += int(dados_m.get("atividades_adaptadas", 0))
        except Exception:
            pass

    nome_turma = turma.nome_turma or f"Turma {id_turma}"
    resumo = (
        f"Turma {nome_turma} com {total_alunos} estudante(s) cadastrado(s), "
        f"{peis_ativos} PEI(s) ativo(s) e "
        f"{adaptacoes_total} atividade(s) adaptada(s)."
    )

    engajamento_area = (
        {"leitura": 64, "matematica": 78, "ciencias": 72}
        if total_alunos > 0
        else {"leitura": 0, "matematica": 0, "ciencias": 0}
    )

    return TurmaDashboardResponse(
        id_turma=id_turma,
        nome_turma=nome_turma,
        total_alunos=total_alunos,
        alunos_com_pei_ativo=peis_ativos,
        adaptacoes_feitas=adaptacoes_total,
        resumo=resumo,
        engajamento_por_area=engajamento_area,
    )
