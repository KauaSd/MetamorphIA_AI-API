from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status

from auth_jwt import validar_permissao_chat_usuario, verificar_token_jwt
from db_client import ResourceNotFoundError, obter_aluno
from guardrails import sanitizar_dados_sensiveis, validar_e_higienizar_entrada
from llm_client import gerar_resposta
from memoria.queries import (
    buscar_documentos_referencia,
    listar_conversas_aluno,
    obter_mensagens_conversa,
)
from schemas import ChatRequest, ChatResponse, ConversaDetalhe, ConversaResumo, MensagemItem

router = APIRouter(tags=["chat"])

_CONVERSAS_SESSAO: dict[int, dict[str, dict[str, Any]]] = {}


def obter_historico_sessao_aluno(id_aluno: int, limite: int = 20) -> str:
    convs = _CONVERSAS_SESSAO.get(id_aluno, {})
    msgs: list[dict[str, Any]] = []
    for c in convs.values():
        msgs.extend(c.get("mensagens", []))
    msgs.sort(key=lambda x: x.get("data", ""))
    linhas = []
    for m in msgs[-limite:]:
        remetente = "Professor" if m.get("role") == "user" else "Assistente"
        conteudo = m.get("conteudo", "")
        if remetente == "Assistente" and len(conteudo) > 300:
            conteudo = conteudo[:300] + "..."
        linhas.append(f"{remetente}: {conteudo}")
    return "\n".join(linhas)


@router.post("/alunos/{id_aluno}/chat", response_model=ChatResponse)
async def post_chat(
    id_aluno: int,
    payload: ChatRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ChatResponse:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(
        usuario_autenticado.get("id_prof")
        or usuario_autenticado.get("sub")
        or payload.id_prof
        or 1
    )

    try:
        aluno = await obter_aluno(id_aluno)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aluno {id_aluno} não encontrado na base de dados.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro de comunicação com a API-DB: {exc}",
        )

    cid = payload.conversa_id
    if cid in ("string", "", "null", "None"):
        cid = None
    conversa_id = cid or str(uuid.uuid4())

    valido, msg_segura, erro_guardrail = validar_e_higienizar_entrada(payload.mensagem)
    if not valido:
        return ChatResponse(resposta=erro_guardrail, conversa_id=conversa_id)

    convs_aluno = _CONVERSAS_SESSAO.setdefault(id_aluno, {})
    if payload.historico is not None:
        mensagens_anteriores = payload.historico
    elif cid:
        conv_mem = convs_aluno.get(conversa_id)
        if conv_mem:
            if conv_mem.get("id_prof") != id_prof_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Esta conversa pertence a outro professor.",
                )
            mensagens_anteriores = list(conv_mem.get("mensagens", []))
        else:
            msgs_qualquer_prof = obter_mensagens_conversa(id_aluno=id_aluno, conversa_id=conversa_id)
            msgs_deste_prof = obter_mensagens_conversa(
                id_aluno=id_aluno, conversa_id=conversa_id, id_prof=id_prof_token
            )
            if msgs_qualquer_prof and not msgs_deste_prof:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Esta conversa pertence a outro professor.",
                )
            mensagens_anteriores = msgs_deste_prof
    else:
        mensagens_anteriores = []
    historico_conversa = ""
    eh_conversa_em_andamento = bool(mensagens_anteriores)

    if mensagens_anteriores:
        linhas_historico = []
        for m in mensagens_anteriores[-6:]:
            remetente = "Professor" if m.get("role") == "user" else "Assistente"
            conteudo, _ = sanitizar_dados_sensiveis(m.get("conteudo", ""))
            if remetente == "Assistente" and len(conteudo) > 400:
                conteudo = conteudo[:400] + "..."
            linhas_historico.append(f"{remetente}: {conteudo}")
        historico_conversa = "HISTÓRICO DA CONVERSA:\n" + "\n".join(linhas_historico) + "\n\n"

    perfil_contexto = aluno.perfil_ia or aluno.desc_aluno or ""
    contexto_combinado = ""
    if perfil_contexto:
        perfil_contexto, _ = sanitizar_dados_sensiveis(perfil_contexto)
        contexto_combinado = f"PERFIL E CONTEXTO DO ALUNO:\n{perfil_contexto}"

    fontes_docs = buscar_documentos_referencia(query=msg_segura, n_results=2) if not eh_conversa_em_andamento else []

    if eh_conversa_em_andamento:
        instrucao_continuidade = (
            "- ATENÇÃO: Esta é uma MENSAGEM DE CONTINUAÇÃO de uma conversa já em andamento.\n"
            "- NÃO use saudações repetitivas (como 'Olá, professor!', 'Oi, professor!'). Entre direto na resposta de forma natural e fluida, como em um chat real.\n"
            "- Responda diretamente e com simpatia ao comentário ou pergunta do professor, mantendo o assunto e o contexto que já estavam combinando (ex: atividade de frações).\n"
            "- Fale de forma leve, prática e conversacional, como um professor parceiro trocando ideias com um colega.\n"
            "- NÃO recomece o plano do zero e NÃO gere tabelas gigantescas ou ementas formais a cada mensagem simples."
        )
    else:
        instrucao_continuidade = (
            "- Esta é a PRIMEIRA mensagem desta conversa.\n"
            "- Dê uma breve saudação inicial calorosa e acolhedora ao professor e apresente propostas práticas, simples e criativas para a atividade do aluno."
        )

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    desc_aluno_limpa, _ = sanitizar_dados_sensiveis(aluno.desc_aluno or "")

    prompt = (
        f"Você é o assistente pedagógico parceiro da MetamorphIA, conversando amigavelmente com o professor sobre o estudante:\n"
        f"- Nome: {nome_aluno_limpo}\n"
        f"- Idade: {aluno.idade_aluno or 'Não informada'}\n"
        f"- Diagnóstico / Condição: {aluno.neurodiv_aluno}\n"
        f"- Hiperfocos e Observações: {desc_aluno_limpa or 'Nenhuma'}\n\n"
        f"{historico_conversa}"
        f"Mensagem Atual do Professor:\n{msg_segura}\n\n"
        f"Diretrizes para a sua resposta:\n"
        f"1. Seja simpático, acolhedor e próximo, como um verdadeiro parceiro/professor assistente.\n"
        f"2. {instrucao_continuidade}\n"
        f"3. Autoralidade Pedagógica: Transforme as diretrizes e metodologias (DUA, WWC, etc.) em ideias pedagógicas autorais e originais criadas sob medida para o aluno, sem copiar trechos brutos dos documentos.\n"
        f"4. Citação Orgânica ou Linha Discreta: Cite a fonte de apoio (presente nos metadados) de forma orgânica no meio do texto (ex: 'baseado nas diretrizes do DUA 3.0 / CAST') ou em apenas 1 linha discreta em itálico no final: '*Fonte base: [nome da fonte]*', citando estritamente os materiais utilizados nesta resposta.\n"
        f"5. Fim do Rodapé Fixo: NUNCA adicione blocos fixos ou listas burocráticas de referências ao final da resposta.\n"
        f"6. Proponha ideias práticas e viáveis para a sala de aula real, com linguagem simples e acessível.\n"
        f"7. Evite tom burocrático ou juridicista: aplique a inclusão e acessibilidade naturalmente, sem ficar recitando leis ou decretos a todo momento.\n"
        f"8. Privacidade LGPD: Jamais armazene, solicite ou faça referência a documentos de identificação pessoal (CPF, RG, etc.)."
    )

    resposta_ia = gerar_resposta(
        prompt=prompt,
        contexto_rag=contexto_combinado,
        fontes_referencia=fontes_docs,
        formato_referencias="discreto",
    )

    agora_iso = datetime.now(timezone.utc).isoformat()
    if mensagens_anteriores:
        primeira_msg_user = next((m for m in mensagens_anteriores if m.get("role") == "user"), None)
        titulo_bruto = primeira_msg_user.get("conteudo") if primeira_msg_user else msg_segura
    else:
        titulo_bruto = msg_segura
    titulo_resumo, _ = sanitizar_dados_sensiveis(titulo_bruto[:40])

    conv_atual = convs_aluno.setdefault(
        conversa_id,
        {
            "conversa_id": conversa_id,
            "id_prof": id_prof_token,
            "titulo": titulo_resumo,
            "data": agora_iso,
            "mensagens": [],
        },
    )
    conv_atual["data"] = agora_iso
    conv_atual["mensagens"].extend(
        [
            {"role": "user", "conteudo": msg_segura, "data": agora_iso},
            {"role": "assistant", "conteudo": resposta_ia, "data": agora_iso},
        ]
    )

    if payload.total_interacoes is not None:
        total_interacoes = payload.total_interacoes
    else:
        total_interacoes = sum(
            1
            for c in convs_aluno.values()
            for m in c.get("mensagens", [])
            if m.get("role") == "user"
        )

    from api.routes.dashboard_aluno import verificar_e_atualizar_contexto_aluno

    historico_para_sintese = (
        f"{historico_conversa}Professor: {msg_segura}\nAssistente: {resposta_ia[:300]}"
        if payload.historico is not None
        else obter_historico_sessao_aluno(id_aluno)
    )
    perfil_atualizado = await verificar_e_atualizar_contexto_aluno(
        aluno=aluno,
        historico_conversas=historico_para_sintese,
        total_interacoes=total_interacoes,
    )

    return ChatResponse(
        resposta=resposta_ia,
        conversa_id=conversa_id,
        perfil_atualizado=perfil_atualizado,
    )


@router.get("/alunos/{id_aluno}/conversas", response_model=list[ConversaResumo])
async def get_conversas(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> list[ConversaResumo]:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    conversas = listar_conversas_aluno(id_aluno, id_prof=id_prof_token)
    return [ConversaResumo(**c) for c in conversas]


@router.get("/alunos/{id_aluno}/conversas/{conversa_id}", response_model=ConversaDetalhe)
async def get_mensagens_conversa_endpoint(
    id_aluno: int,
    conversa_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ConversaDetalhe:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    msgs = obter_mensagens_conversa(id_aluno, conversa_id, id_prof=id_prof_token)
    return ConversaDetalhe(
        conversa_id=conversa_id,
        mensagens=[MensagemItem(**m) for m in msgs],
    )
