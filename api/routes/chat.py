from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import uuid
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status

from arquivo_utils import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    decodificar_base64_ou_data_url,
    extrair_texto_arquivo,
    montar_docx_atividade_adaptada,
    montar_pdf_atividade_adaptada,
    para_data_url_docx,
    para_data_url_pdf,
)
from templates_atividades import (
    identificar_template_na_mensagem,
    obter_template_por_id,
    recomendar_templates_para_perfil,
)
from auth_jwt import validar_permissao_chat_usuario, verificar_token_jwt
from cache_utils import BoundedTTLCache
from db_client import (
    baixar_pei_docx_db,
    excluir_conversa_aluno_db,
    levantar_http_de_erro_db,
    listar_conversas_aluno_db,
    listar_conversas_lote_db,
    listar_peis_aluno_db,
    obter_aluno,
    obter_mensagens_conversa_db,
    obter_pei_detalhe_db,
    obter_turma,
    registrar_turno_conversa_db,
    salvar_pei_arquivo_db,
)
from guardrails import (
    MENSAGEM_RECUSA_ESCOPO,
    eh_pedido_explicitamente_fora_escopo,
    sanitizar_dados_sensiveis,
    validar_escopo_pedagogico,
)
from llm_client import gerar_resposta_async
from memoria.queries import buscar_documentos_referencia
from schemas import (
    AnexoItem,
    ChatRequest,
    ChatResponse,
    ConversaDetalhe,
    ConversaResumo,
    IndicadorDesenvolvimentoItem,
    MensagemItem,
    MetaBimestreItem,
)

router = APIRouter(tags=["chat"])

# Estado efêmero com TTL (1h) e limite de itens para evitar crescimento ilimitado em RAM
_PEDIDOS_ATIVIDADE_PENDENTES: BoundedTTLCache[tuple[int, int, str], dict[str, Any]] = BoundedTTLCache(ttl_seconds=1800, max_items=200)
_PEDIDOS_REVISAO_PEI_PENDENTES: BoundedTTLCache[tuple[int, int, str], dict[str, Any]] = BoundedTTLCache(ttl_seconds=1800, max_items=200)
_ATIVIDADES_DOCX_MEMORIA: BoundedTTLCache[str, tuple[int, str, bytes]] = BoundedTTLCache(ttl_seconds=3600, max_items=100)
_ATIVIDADES_PDF_MEMORIA: BoundedTTLCache[str, tuple[int, str, bytes]] = BoundedTTLCache(ttl_seconds=3600, max_items=100)

_TERMOS_PEDIDO_ATIVIDADE = (
    "criar uma atividade",
    "criar atividade",
    "crie uma atividade",
    "gerar uma atividade",
    "gerar atividade",
    "gere uma atividade",
    "fazer uma atividade",
    "faça uma atividade",
    "quero uma atividade",
    "monte uma atividade",
    "montar uma atividade",
    "elabore uma atividade",
    "elaborar uma atividade",
    "atividade adaptada",
    "adaptar uma atividade",
)


def _detectar_pedido_atividade_conversa(texto: str) -> bool:
    t = (texto or "").strip().lower()
    return any(termo in t for termo in _TERMOS_PEDIDO_ATIVIDADE)


_TERMOS_REVISAO_PEI = (
    "trocar no pei",
    "trocar algo no pei",
    "trocar uma coisa no pei",
    "alterar o pei",
    "alterar no pei",
    "mudar o pei",
    "mudar no pei",
    "modificar o pei",
    "modificar no pei",
    "atualizar o pei",
    "editar o pei",
    "revisar o pei",
    "refazer o pei",
    "ajustar o pei",
    "ajustar no pei",
    "novo pei",
)


def _detectar_pedido_revisao_pei(texto: str) -> bool:
    t = (texto or "").strip().lower()
    if any(termo in t for termo in _TERMOS_REVISAO_PEI):
        return True
    verbos_edicao = ("trocar", "alterar", "mudar", "modificar", "atualizar", "editar", "ajustar", "incluir", "remover", "substituir")
    if "pei" in t and any(v in t for v in verbos_edicao):
        return True
    if any(v in t for v in verbos_edicao) and any(
        alvo in t for alvo in ("resposta 1", "resposta 2", "resposta 3", "resposta 4", "a resposta", "parte 1", "parte 2", "parte 3", "parte 4")
    ):
        return True
    return False


def _eh_comando_documento_pei(texto: str) -> bool:
    t = (texto or "").strip().lower().strip(".!?,; ")
    if t in ("documento", "gerar documento", "baixar pei", "enviar documento"):
        return True
    if ("documento" in t or ".docx" in t) and any(
        k in t for k in ("gere", "gerar", "envie", "enviar", "baixar", "pei")
    ):
        return True
    return False


def _classificar_confirmacao_revisao_pei(texto: str) -> str:
    t = (texto or "").strip().lower().strip(".!?,; ")
    if _eh_comando_documento_pei(t):
        return "gerar_documento"
    negativos = {"não", "nao", "n", "cancelar", "cancela", "não quero", "nao quero", "deixa", "desistir"}
    if t in negativos or t.startswith(("não,", "nao,", "cancelar")):
        return "cancelado"
    return "ajuste"


def _exigir_pei_valido_aluno(aluno) -> None:
    """Garante que o aluno possui PEI concluído e dentro da validade bimestral (60 dias) antes do chat."""
    if not aluno.pei_ativo:
        if aluno.pei_feito:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A validade bimestral (60 dias) do PEI deste aluno expirou. Renove o PEI antes de conversar no chat.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="É obrigatório concluir o PEI deste aluno antes de conversar com a IA.",
        )


@router.post("/alunos/{id_aluno}/chat", response_model=ChatResponse)
async def post_chat(
    id_aluno: int,
    payload: ChatRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ChatResponse:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_raw = usuario_autenticado.get("id_prof")
    if id_prof_raw is None:
        id_prof_raw = usuario_autenticado["sub"]
    id_prof_token = int(id_prof_raw)
    token_jwt = usuario_autenticado.get("_raw_token")

    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado na base de dados.")

    _exigir_pei_valido_aluno(aluno)

    cid = payload.conversa_id
    if cid in ("string", "", "null", "None"):
        cid = None
    conversa_id = cid or str(uuid.uuid4())
    chave_pendente = (id_prof_token, id_aluno, conversa_id)

    anexos_recebidos = payload.anexos or []
    textos_anexos: list[str] = []
    nomes_anexos: list[str] = []
    for anexo in anexos_recebidos:
        nomes_anexos.append(anexo.name)
        if anexo.dataUrl:
            raw_bytes = decodificar_base64_ou_data_url(anexo.dataUrl)
            texto_extraido = extrair_texto_arquivo(
                nome_arquivo=anexo.name,
                conteudo_bytes=raw_bytes,
                mime_type=anexo.type or "",
            )
            if texto_extraido:
                texto_limpo, _ = sanitizar_dados_sensiveis(texto_extraido)
                textos_anexos.append(f"[Arquivo: {anexo.name}]\n{texto_limpo[:4000]}")
        else:
            textos_anexos.append(f"[Arquivo anexado pelo professor: {anexo.name}]")

    mensagem_base = (payload.mensagem or "").strip()
    if not mensagem_base and nomes_anexos:
        mensagem_base = f"Adapte a atividade do arquivo anexado ({', '.join(nomes_anexos)}) para este estudante."

    pedido_pendente = _PEDIDOS_ATIVIDADE_PENDENTES.get(chave_pendente)
    revisao_pei_pendente = _PEDIDOS_REVISAO_PEI_PENDENTES.get(chave_pendente)
    texto_para_validacao = mensagem_base
    if textos_anexos or pedido_pendente or revisao_pei_pendente or payload.template_id or payload.modo_adaptacao:
        texto_para_validacao = f"{mensagem_base} (adaptação de atividade pedagógica e PEI para o aluno)"

    from guardrails import detectar_prompt_injection

    if not mensagem_base:
        return ChatResponse(
            resposta="Por favor, digite sua pergunta pedagógica ou solicitação de adaptação.",
            conversa_id=conversa_id,
        )
    eh_injection, alerta_injection = detectar_prompt_injection(mensagem_base)
    if eh_injection:
        return ChatResponse(resposta=alerta_injection, conversa_id=conversa_id)
    msg_segura, _ = sanitizar_dados_sensiveis(mensagem_base)

    if payload.historico is not None:
        mensagens_anteriores = payload.historico
    elif cid:
        try:
            mensagens_anteriores = await obter_mensagens_conversa_db(
                id_aluno=id_aluno, conversa_id=conversa_id, token=token_jwt
            )
        except Exception as exc:
            levantar_http_de_erro_db(exc, msg_nao_encontrado="Conversa não encontrada.")
    else:
        mensagens_anteriores = []

    historico_conversa = ""
    eh_conversa_em_andamento = bool(mensagens_anteriores)

    if eh_pedido_explicitamente_fora_escopo(mensagem_base):
        return ChatResponse(resposta=MENSAGEM_RECUSA_ESCOPO, conversa_id=conversa_id)

    if not eh_conversa_em_andamento:
        no_escopo, msg_escopo = validar_escopo_pedagogico(texto_para_validacao)
        if not no_escopo:
            return ChatResponse(resposta=msg_escopo, conversa_id=conversa_id)

    if mensagens_anteriores:
        linhas_historico = []
        for m in mensagens_anteriores[-6:]:
            remetente = "Professor" if m.get("role") == "user" else "Assistente"
            conteudo, _ = sanitizar_dados_sensiveis(m.get("conteudo", ""))
            if remetente == "Assistente" and len(conteudo) > 400:
                conteudo = conteudo[:400] + "..."
            linhas_historico.append(f"{remetente}: {conteudo}")
        historico_conversa = "HISTÓRICO DESTA CONVERSA:\n" + "\n".join(linhas_historico) + "\n\n"

    # Memória geral consolidada do aluno vinda exclusivamente de Aluno.perfil_ia na API-DB
    perfil_contexto = aluno.perfil_ia or aluno.desc_aluno or ""
    contexto_combinado = ""
    if perfil_contexto:
        perfil_contexto, _ = sanitizar_dados_sensiveis(perfil_contexto)
        contexto_combinado = f"MEMÓRIA E PERFIL CONSOLIDADO DO ALUNO (PEI + CONVERSAS):\n{perfil_contexto}"

    fontes_docs = (
        await asyncio.to_thread(buscar_documentos_referencia, query=msg_segura, n_results=2)
        if not eh_conversa_em_andamento
        else []
    )

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
            "- Dê uma breve saudação inicial calorosa e acolhedora ao professor e apresente propostas práticas, simples e criativas para a atividade do aluno, aproveitando a memória consolidada do estudante."
        )

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    desc_aluno_limpa, _ = sanitizar_dados_sensiveis(aluno.desc_aluno or "")

    # Fluxo 1: Confirmação ou ajuste de revisão de PEI pendente
    if isinstance(revisao_pei_pendente, dict) and not anexos_recebidos:
        decisao = _classificar_confirmacao_revisao_pei(msg_segura)
        if decisao == "cancelado":
            _PEDIDOS_REVISAO_PEI_PENDENTES.pop(chave_pendente, None)
            resp_cancel = "Tudo bem! Mantive o PEI atual sem alterações. Se quiser ajustar outro ponto depois, é só me avisar."
            if not payload.persistido_pelo_gateway:
                try:
                    await registrar_turno_conversa_db(
                        id_aluno=id_aluno, conversa_id=conversa_id,
                        mensagem_usuario=msg_segura, resposta_ia=resp_cancel,
                        titulo="Revisão de PEI", token=token_jwt,
                    )
                except Exception:
                    pass
            return ChatResponse(resposta=resp_cancel, conversa_id=conversa_id)

        if decisao == "gerar_documento":
            from api.routes.dashboard_aluno import gerar_contexto_sintetizado_aluno
            from api.routes.pei import (
                formatar_texto_pei_completo,
                gerar_respostas_gerais_pei,
                montar_docx_pei,
            )
            from schemas import PeiRespostaItem

            _PEDIDOS_REVISAO_PEI_PENDENTES.pop(chave_pendente, None)
            componente_rev = revisao_pei_pendente.get("componente") or "Língua Portuguesa"
            ano_rev = revisao_pei_pendente.get("ano_escolar") or "Ensino Fundamental"
            bimestre_rev = int(revisao_pei_pendente.get("bimestre") or 1)
            solicitacao_rev = revisao_pei_pendente.get("solicitacao") or ""
            proposta_rev = revisao_pei_pendente.get("proposta") or ""
            pei_anterior_rev = revisao_pei_pendente.get("pei_anterior") or ""

            fontes_pei = await asyncio.to_thread(
                buscar_documentos_referencia,
                query=f"Currículo Paulista {componente_rev} {ano_rev} PEI adaptações curriculares DUA",
                n_results=2,
            )
            textos_rag_pei = "\n\n".join(f"[{f['titulo']}]: {f['texto']}" for f in fontes_pei)
            respostas_sub = [
                PeiRespostaItem(pergunta="Alteração solicitada pelo professor", resposta=solicitacao_rev),
                PeiRespostaItem(pergunta="Proposta aprovada para o novo PEI", resposta=proposta_rev[:2000]),
            ]
            contexto_rev = f"{perfil_contexto}\n\nPEI ANTERIOR (resumo):\n{pei_anterior_rev[:1500]}".strip()
            respostas_gerais_int = await asyncio.to_thread(
                gerar_respostas_gerais_pei,
                nome_aluno=nome_aluno_limpo,
                idade_aluno=aluno.idade_aluno,
                neurodivergencia=aluno.neurodiv_aluno,
                contexto_aluno=contexto_rev,
                respostas_subperguntas=respostas_sub,
                componente_curricular=componente_rev,
                ano_escolar=ano_rev,
                periodo_bimestre=bimestre_rev,
                contexto_rag=textos_rag_pei,
                fontes_docs=fontes_pei,
            )
            prof_nome = str(usuario_autenticado.get("nome_prof") or "")
            doc_gerado = formatar_texto_pei_completo(
                nome_aluno=nome_aluno_limpo,
                nome_professor_regente=prof_nome,
                nome_professor_especializado="",
                componente_curricular=componente_rev,
                periodo_bimestre=bimestre_rev,
                respostas_gerais=respostas_gerais_int,
            )
            docx_bytes = montar_docx_pei(
                nome_aluno=nome_aluno_limpo,
                respostas_gerais=respostas_gerais_int,
                nome_professor_regente=prof_nome,
                nome_professor_especializado="",
                componente_curricular=componente_rev,
                periodo_bimestre=bimestre_rev,
            )
            perfil_sint, _, inds_raw, metas_raw = await gerar_contexto_sintetizado_aluno(
                aluno=aluno,
                contexto_pei=doc_gerado,
                sincronizar_db=False,
                token=token_jwt,
            )
            novo_pei_id = str(uuid.uuid4())
            nome_seg = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in nome_aluno_limpo).strip("_") or f"aluno_{id_aluno}"
            arq_nome = f"PEI_{nome_seg}_{bimestre_rev}Bim_2026.docx"
            docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
            try:
                await salvar_pei_arquivo_db(
                    id_aluno=id_aluno,
                    pei_id=novo_pei_id,
                    arquivo_nome=arq_nome,
                    documento_docx_base64=docx_b64,
                    componente_curricular=componente_rev,
                    perfil_ia=perfil_sint,
                    indicadores_desenvolvimento=inds_raw,
                    metas_bimestre=metas_raw,
                    validade_dias=60,
                    token=token_jwt,
                )
            except Exception:
                pass

            resp_ok = (
                f"Pronto! Gere e salvei o **novo PEI atualizado de {nome_aluno_limpo}** ({componente_rev}) "
                f"com a alteração confirmada. Você já pode baixar o arquivo `.docx` abaixo ou acessá-lo no menu de PEIs."
            )
            anexo_pei = AnexoItem(
                name=arq_nome,
                type=DOCX_MEDIA_TYPE,
                size=len(docx_bytes),
                dataUrl=para_data_url_docx(docx_bytes),
            )
            if not payload.persistido_pelo_gateway:
                try:
                    await registrar_turno_conversa_db(
                        id_aluno=id_aluno, conversa_id=conversa_id,
                        mensagem_usuario=msg_segura, resposta_ia=resp_ok,
                        titulo=f"Novo PEI - {componente_rev}",
                        anexos_ia=[anexo_pei.model_dump()],
                        token=token_jwt,
                    )
                except Exception:
                    pass
            return ChatResponse(
                resposta=resp_ok,
                conversa_id=conversa_id,
                perfil_atualizado=perfil_sint,
                indicadores_desenvolvimento=[IndicadorDesenvolvimentoItem(**i) for i in inds_raw],
                metas_bimestre=[MetaBimestreItem(**m) for m in metas_raw],
                anexos=[anexo_pei],
                arquivo_nome=arq_nome,
                documento_docx_base64=docx_b64,
                download_url=f"/alunos/{id_aluno}/pei/{novo_pei_id}/docx",
            )

    # Fluxo 2: Pedido para alterar/trocar algo no PEI (ou ajuste da proposta pendente)
    if not anexos_recebidos and (
        _detectar_pedido_revisao_pei(msg_segura)
        or (isinstance(revisao_pei_pendente, dict) and _classificar_confirmacao_revisao_pei(msg_segura) == "ajuste")
    ):
        pei_anterior_txt = ""
        if isinstance(revisao_pei_pendente, dict):
            pei_anterior_txt = revisao_pei_pendente.get("pei_anterior") or ""
        else:
            try:
                peis_lista = await listar_peis_aluno_db(id_aluno, token=token_jwt)
                if peis_lista:
                    det = await obter_pei_detalhe_db(id_aluno, peis_lista[0]["pei_id"], token=token_jwt)
                    pei_anterior_txt = str(det.get("documento_gerado") or "")
            except Exception:
                pei_anterior_txt = ""

        ano_turma = "Ensino Fundamental"
        if aluno.id_turma:
            try:
                turma_obj = await obter_turma(aluno.id_turma, token=token_jwt)
                ano_turma = turma_obj.serie_ano or turma_obj.nome_turma or ano_turma
            except Exception:
                pass

        componente_rev = "Língua Portuguesa"
        for tag_prof in (usuario_autenticado.get("tags") or []):
            if str(tag_prof).lower() in msg_segura.lower():
                componente_rev = str(tag_prof)
                break

        prompt_revisao = (
            f"O professor deseja alterar o PEI de {nome_aluno_limpo} ({aluno.neurodiv_aluno}, {ano_turma}).\n"
            f"Perfil/Memória atual do aluno:\n{perfil_contexto[:1500]}\n\n"
            f"PEI atual (trecho):\n{pei_anterior_txt[:2000] or 'Não disponível'}\n\n"
            f"Solicitação do professor: {msg_segura}\n\n"
            "Apresente de forma clara e objetiva:\n"
            "1. O que será alterado no PEI.\n"
            "2. A nova proposta de redação/estratégia para essa parte do PEI.\n"
            "Ao final, oriente o professor a digitar \"documento\" quando quiser gerar o novo PEI em .docx, ou enviar mais ajustes."
        )
        proposta_ia = await gerar_resposta_async(
            prompt=prompt_revisao,
            fontes_referencia=[],
            aplicar_guardrails=False,
        )
        if "documento" not in proposta_ia.lower():
            proposta_ia = (
                f"{proposta_ia}\n\n"
                "Se quiser ajustar mais alguma coisa antes, é só me dizer. "
                "Quando estiver pronto, digite **\"documento\"** para eu gerar e enviar o novo PEI!"
            )
        _PEDIDOS_REVISAO_PEI_PENDENTES[chave_pendente] = {
            "solicitacao": (
                f"{revisao_pei_pendente.get('solicitacao', '')}\nAjuste adicional: {msg_segura}".strip()
                if isinstance(revisao_pei_pendente, dict)
                else msg_segura
            ),
            "proposta": proposta_ia,
            "pei_anterior": pei_anterior_txt,
            "componente": componente_rev,
            "ano_escolar": ano_turma,
            "bimestre": 1,
        }
        if not payload.persistido_pelo_gateway:
            try:
                await registrar_turno_conversa_db(
                    id_aluno=id_aluno, conversa_id=conversa_id,
                    mensagem_usuario=msg_segura, resposta_ia=proposta_ia,
                    titulo="Revisão de PEI", token=token_jwt,
                )
            except Exception:
                pass
        return ChatResponse(resposta=proposta_ia, conversa_id=conversa_id)

    opcoes_pendentes_ids = (
        [o["id"] for o in pedido_pendente.get("opcoes", [])]
        if isinstance(pedido_pendente, dict)
        else None
    )

    template_escolhido = obter_template_por_id(payload.template_id)
    if not template_escolhido and not anexos_recebidos:
        if pedido_pendente or payload.modo_adaptacao == "template":
            template_escolhido = identificar_template_na_mensagem(
                msg_segura, opcoes_pendentes_ids
            )

    pediu_atividade_na_conversa = (
        not anexos_recebidos
        and not template_escolhido
        and (
            payload.modo_adaptacao == "template"
            or _detectar_pedido_atividade_conversa(msg_segura)
        )
    )

    if pediu_atividade_na_conversa:
        opcoes_recomendadas = recomendar_templates_para_perfil(
            neurodivergencia=aluno.neurodiv_aluno,
            desc_aluno=perfil_contexto or desc_aluno_limpa,
            limite=3,
        )
        linhas_opcoes = "\n".join(
            f"**{op['opcao']}. {op['nome']}** — {op['descricao']}"
            for op in opcoes_recomendadas
        )
        resposta_escolha = (
            f"Com certeza! Para criar a atividade adaptada para **{nome_aluno_limpo}** "
            f"(*{aluno.neurodiv_aluno}*), selecionei as melhores abordagens considerando o perfil dele:\n\n"
            f"{linhas_opcoes}\n\n"
            f"Qual dessas opções você acha melhor usarmos para esta atividade? "
            f"(Pode responder com o número da opção ou o nome do modelo!)"
        )
        _PEDIDOS_ATIVIDADE_PENDENTES[chave_pendente] = {
            "tema": msg_segura,
            "opcoes": opcoes_recomendadas,
        }
        if not payload.persistido_pelo_gateway:
            titulo_resumo, _ = sanitizar_dados_sensiveis(msg_segura[:40])
            try:
                await registrar_turno_conversa_db(
                    id_aluno=id_aluno,
                    conversa_id=conversa_id,
                    mensagem_usuario=msg_segura,
                    resposta_ia=resposta_escolha,
                    titulo=titulo_resumo,
                    token=token_jwt,
                )
            except Exception:
                pass
        return ChatResponse(
            resposta=resposta_escolha,
            conversa_id=conversa_id,
            opcoes_adaptacao=opcoes_recomendadas,
        )

    modo_adaptacao_usado: str | None = None
    template_usado_id: str | None = None
    tema_pendente = ""
    if isinstance(pedido_pendente, dict):
        tema_pendente = pedido_pendente.get("tema", "")

    bloco_arquivos_prompt = ""
    if textos_anexos:
        modo_adaptacao_usado = "fiel"
        bloco_arquivos_prompt = (
            "CONTEÚDO DO(S) ARQUIVO(S) ENVIADO(S) PELO PROFESSOR PARA ADAPTAÇÃO (MODO FIEL AO ARQUIVO ORIGINAL):\n"
            + "\n\n".join(textos_anexos)
            + "\n\n"
            "IMPORTANTE (MODO FIEL AO ARQUIVO ORIGINAL): O professor enviou um arquivo PDF/DOCX de atividade. "
            "Você DEVE manter a estrutura, a ordem e todas as questões originais do arquivo enviado, "
            "adaptando apenas a linguagem, o fracionamento dos enunciados, os apoios visuais/concretos e a acessibilidade "
            "para o perfil do estudante. Sua resposta será entregue ao professor em arquivo (.docx / .pdf) pronto para download.\n\n"
        )
    elif template_escolhido:
        modo_adaptacao_usado = "template"
        template_usado_id = template_escolhido["id"]
        _PEDIDOS_ATIVIDADE_PENDENTES.pop(chave_pendente, None)
        contexto_tema = (
            f"Tema solicitado anteriormente pelo professor: {tema_pendente}\n"
            if tema_pendente
            else ""
        )
        bloco_arquivos_prompt = (
            f"CRIAÇÃO DE ATIVIDADE ADAPTADA BASEADA EM TEMPLATE PEDAGÓGICO:\n"
            f"Template escolhido pelo professor: {template_escolhido['nome']} ({template_escolhido['id']})\n"
            f"{contexto_tema}"
            f"{template_escolhido['instrucao_prompt']}\n\n"
            "IMPORTANTE: Gere a atividade adaptada completa, estruturada exatamente segundo o template acima e pronta "
            "para aplicação com o estudante, pois ela será entregue em arquivo (.docx / .pdf) para download.\n\n"
        )

    bloco_instrucoes_prof = ""
    if payload.instrucoes_ia and payload.instrucoes_ia.strip():
        inst_limpa, _ = sanitizar_dados_sensiveis(payload.instrucoes_ia.strip())
        bloco_instrucoes_prof = (
            f"INSTRUÇÕES PERSONALIZADAS DO PROFESSOR:\n{inst_limpa[:2000]}\n\n"
        )

    prompt = (
        f"Você é o assistente pedagógico parceiro da MetamorphIA, conversando amigavelmente com o professor sobre o estudante:\n"
        f"- Nome: {nome_aluno_limpo}\n"
        f"- Idade: {aluno.idade_aluno or 'Não informada'}\n"
        f"- Diagnóstico / Condição: {aluno.neurodiv_aluno}\n"
        f"- Hiperfocos e Observações Iniciais: {desc_aluno_limpa or 'Consulte o Perfil Consolidado'}\n\n"
        f"{bloco_instrucoes_prof}"
        f"{historico_conversa}"
        f"{bloco_arquivos_prompt}"
        f"Mensagem Atual do Professor:\n{msg_segura}\n\n"
        f"Diretrizes para a sua resposta:\n"
        f"1. Seja simpático, acolhedor e próximo, como um verdadeiro parceiro/professor assistente.\n"
        f"2. {instrucao_continuidade}\n"
        f"3. Autoralidade Pedagógica: Transforme as diretrizes e metodologias (DUA, WWC, etc.) em ideias pedagógicas autorais e originais criadas sob medida para o aluno, sem copiar trechos brutos dos documentos.\n"
        f"4. Citação Orgânica ou Linha Discreta: Cite a fonte de apoio (presente nos metadados) de forma orgânica no meio do texto (ex: 'baseado nas diretrizes do DUA 3.0 / CAST') ou em apenas 1 linha discreta em itálico no final: '*Fonte base: [nome da fonte]*', citando estritamente os materiais utilizados nesta resposta.\n"
        f"5. Fim do Rodapé Fixo: NUNCA adicione blocos fixos ou listas burocráticas de referências ao final da resposta.\n"
        f"6. Proponha ideias práticas e viáveis para a sala de aula real, com linguagem simples e acessível.\n"
        f"7. Evite tom burocrático ou juridicista: aplique a inclusão e acessibilidade naturalmente, sem ficar recitando leis ou decretos a todo momento.\n"
        f"8. Privacidade LGPD: Jamais armazene, solicite ou faça referência a documentos de identificação pessoal (CPF, RG, etc.).\n"
        f"9. Memória e PEI do Aluno: Sempre consulte e respeite o Perfil Consolidado (PEI + aprendizados de conversas anteriores) fornecido no contexto.\n"
        f"10. Veracidade de Recursos: JAMAIS invente títulos fictícios de vídeos do YouTube, livros, canais, autores ou links inexistentes; ao recomendar vídeos ou materiais, indique apenas o formato, a proposta pedagógica e termos de busca.\n"
        f"11. Escopo Estrito de Educação Inclusiva: Atue exclusivamente no apoio pedagógico inclusivo ao professor (adaptação de atividades, DUA e PEI). NUNCA faça trabalhos escolares, tarefas de casa ou correções gramaticais/ortográficas avulsas; se o pedido fugir desse escopo, recuse gentilmente e redirecione."
    )

    pediu_documento_pei = _eh_comando_documento_pei(msg_segura)
    deve_gerar_arquivo_atividade = bool(
        anexos_recebidos
        or template_escolhido is not None
        or payload.gerar_arquivo_atividade
        or any(
            termo in msg_segura.lower()
            for termo in (
                "atividade adaptada em arquivo",
                "gerar arquivo",
                "mandar arquivo",
                "enviar arquivo",
                "baixar atividade",
                "documento da atividade",
            )
        )
    )

    formato_escolhido = (payload.formato_saida or "docx").strip().lower()
    if formato_escolhido not in ("docx", "pdf"):
        formato_escolhido = "docx"
    if "em pdf" in msg_segura.lower() or "formato pdf" in msg_segura.lower():
        formato_escolhido = "pdf"

    anexos_resposta: list[AnexoItem] = []
    atividade_id_gerada: str | None = None
    arquivo_nome_gerado: str | None = None
    docx_b64_gerado: str | None = None
    pdf_b64_gerado: str | None = None
    download_url_gerado: str | None = None
    formato_arquivo_gerado: str | None = None

    if pediu_documento_pei:
        from api.routes.pei import montar_docx_pei

        peis_existentes = await listar_peis_aluno_db(id_aluno, token=token_jwt)
        docx_pei_bytes: bytes | None = None
        pei_id_alvo = str(uuid.uuid4())
        if peis_existentes:
            pei_id_alvo = peis_existentes[0]["pei_id"]
            try:
                docx_pei_bytes, _ = await baixar_pei_docx_db(
                    id_aluno, pei_id_alvo, token=token_jwt
                )
            except Exception:
                docx_pei_bytes = None

        if not docx_pei_bytes:
            resumo_mem = perfil_contexto or f"Planejamento curricular adaptado para {nome_aluno_limpo} ({aluno.neurodiv_aluno})."
            docx_pei_bytes = montar_docx_pei(
                nome_aluno=nome_aluno_limpo,
                respostas_gerais={1: resumo_mem, 2: resumo_mem, 3: resumo_mem, 4: resumo_mem},
            )

        nome_seguro = "".join(c if c.isalnum() else "_" for c in nome_aluno_limpo).strip("_") or f"aluno_{id_aluno}"
        arquivo_nome_gerado = f"PEI_{nome_seguro}_2026.docx"
        docx_b64_gerado = base64.b64encode(docx_pei_bytes).decode("ascii")
        download_url_gerado = f"/alunos/{id_aluno}/pei/{pei_id_alvo}/docx"
        anexos_resposta.append(
            AnexoItem(
                name=arquivo_nome_gerado,
                type=DOCX_MEDIA_TYPE,
                size=len(docx_pei_bytes),
                dataUrl=para_data_url_docx(docx_pei_bytes),
            )
        )
        resposta_ia = (
            f"Aqui está o documento oficial do **PEI de {nome_aluno_limpo}** em formato `.docx` pronto para download! 📄✨\n\n"
            f"Você pode clicar no arquivo anexado nesta mensagem para baixar e editar o que desejar."
        )
    else:
        resposta_ia = await gerar_resposta_async(
            prompt=prompt,
            contexto_rag=contexto_combinado,
            fontes_referencia=fontes_docs,
            formato_referencias="discreto",
        )

    if deve_gerar_arquivo_atividade and not pediu_documento_pei:
        atividade_id_gerada = str(uuid.uuid4())
        if nomes_anexos:
            titulo_ativ = nomes_anexos[0].rsplit(".", 1)[0]
        elif tema_pendente:
            titulo_ativ = tema_pendente[:50]
        else:
            titulo_ativ = msg_segura[:50]

        docx_ativ_bytes = montar_docx_atividade_adaptada(
            titulo=titulo_ativ,
            nome_aluno=nome_aluno_limpo,
            neurodivergencia=aluno.neurodiv_aluno,
            conteudo_adaptado=resposta_ia,
        )
        pdf_ativ_bytes = montar_pdf_atividade_adaptada(
            titulo=titulo_ativ,
            nome_aluno=nome_aluno_limpo,
            neurodivergencia=aluno.neurodiv_aluno,
            conteudo_adaptado=resposta_ia,
        )
        nome_seguro = "".join(c if c.isalnum() else "_" for c in nome_aluno_limpo).strip("_") or f"aluno_{id_aluno}"
        nome_docx = f"Atividade_Adaptada_{nome_seguro}.docx"
        nome_pdf = f"Atividade_Adaptada_{nome_seguro}.pdf"
        _ATIVIDADES_DOCX_MEMORIA[atividade_id_gerada] = (id_prof_token, nome_docx, docx_ativ_bytes)
        _ATIVIDADES_PDF_MEMORIA[atividade_id_gerada] = (id_prof_token, nome_pdf, pdf_ativ_bytes)
        docx_b64_gerado = base64.b64encode(docx_ativ_bytes).decode("ascii")
        pdf_b64_gerado = base64.b64encode(pdf_ativ_bytes).decode("ascii")
        formato_arquivo_gerado = formato_escolhido

        if formato_escolhido == "pdf":
            arquivo_nome_gerado = nome_pdf
            download_url_gerado = f"/alunos/{id_aluno}/atividades/{atividade_id_gerada}/pdf"
            anexos_resposta.append(
                AnexoItem(
                    name=nome_pdf,
                    type=PDF_MEDIA_TYPE,
                    size=len(pdf_ativ_bytes),
                    dataUrl=para_data_url_pdf(pdf_ativ_bytes),
                )
            )
        else:
            arquivo_nome_gerado = nome_docx
            download_url_gerado = f"/alunos/{id_aluno}/atividades/{atividade_id_gerada}/docx"
            anexos_resposta.append(
                AnexoItem(
                    name=nome_docx,
                    type=DOCX_MEDIA_TYPE,
                    size=len(docx_ativ_bytes),
                    dataUrl=para_data_url_docx(docx_ativ_bytes),
                )
            )

    if mensagens_anteriores:
        primeira_msg_user = next((m for m in mensagens_anteriores if m.get("role") == "user"), None)
        titulo_bruto = primeira_msg_user.get("conteudo") if primeira_msg_user else msg_segura
    else:
        titulo_bruto = msg_segura
    titulo_resumo, _ = sanitizar_dados_sensiveis((titulo_bruto or "")[:40])

    if not payload.persistido_pelo_gateway:
        try:
            await registrar_turno_conversa_db(
                id_aluno=id_aluno,
                conversa_id=conversa_id,
                mensagem_usuario=msg_segura,
                resposta_ia=resposta_ia,
                titulo=titulo_resumo,
                anexos_usuario=[a.model_dump() for a in anexos_recebidos] if anexos_recebidos else None,
                anexos_ia=[a.model_dump() for a in anexos_resposta] if anexos_resposta else None,
                token=token_jwt,
            )
        except Exception:
            pass

    from api.routes.dashboard_aluno import (
        deve_atualizar_memoria_conversa,
        gerar_contexto_sintetizado_aluno,
    )

    perfil_atualizado: str | None = None
    indicadores_gerados: list[IndicadorDesenvolvimentoItem] | None = None
    metas_geradas: list[MetaBimestreItem] | None = None
    if not pediu_documento_pei and deve_atualizar_memoria_conversa(
        mensagem_usuario=msg_segura,
        resposta_ia=resposta_ia,
        gerou_atividade_adaptada=deve_gerar_arquivo_atividade,
    ):
        trecho_para_sintese = (
            f"{historico_conversa}Professor: {msg_segura}\nAssistente: {resposta_ia[:600]}"
        )
        perfil_atualizado, _, inds_raw, metas_raw = await gerar_contexto_sintetizado_aluno(
            aluno=aluno,
            historico_conversas=trecho_para_sintese,
            sincronizar_db=not payload.persistido_pelo_gateway,
            token=token_jwt,
        )
        indicadores_gerados = [IndicadorDesenvolvimentoItem(**i) for i in inds_raw]
        metas_geradas = [MetaBimestreItem(**m) for m in metas_raw]

    return ChatResponse(
        resposta=resposta_ia,
        conversa_id=conversa_id,
        perfil_atualizado=perfil_atualizado,
        indicadores_desenvolvimento=indicadores_gerados,
        metas_bimestre=metas_geradas,
        anexos=anexos_resposta or None,
        atividade_id=atividade_id_gerada,
        arquivo_nome=arquivo_nome_gerado,
        documento_docx_base64=docx_b64_gerado,
        documento_pdf_base64=pdf_b64_gerado,
        download_url=download_url_gerado,
        modo_adaptacao_usado=modo_adaptacao_usado,
        template_usado=template_usado_id,
        formato_arquivo=formato_arquivo_gerado,
    )


@router.post("/alunos/{id_aluno}/adaptar-atividade", response_model=ChatResponse)
async def adaptar_atividade_aluno_endpoint(
    id_aluno: int,
    payload: ChatRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ChatResponse:
    """Rota dedicada para criação de atividade adaptada de um aluno com envio e retorno de arquivo."""
    payload.gerar_arquivo_atividade = True
    return await post_chat(
        id_aluno=id_aluno,
        payload=payload,
        usuario_autenticado=usuario_autenticado,
    )


@router.get("/alunos/{id_aluno}/atividades/{atividade_id}/docx")
async def baixar_atividade_adaptada_docx(
    id_aluno: int,
    atividade_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa o arquivo .docx de uma atividade adaptada gerada pela IA."""
    validar_permissao_chat_usuario(usuario_autenticado)
    item = _ATIVIDADES_DOCX_MEMORIA.get(atividade_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo de atividade adaptada não encontrado.",
        )
    dono_id, nome_arq, docx_bytes = item
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    if dono_id != id_prof_token and usuario_autenticado.get("tipo_usuario") != "adm_sistema":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para baixar esta atividade.",
        )
    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{nome_arq}"',
            "X-Atividade-Id": atividade_id,
        },
    )


@router.get("/alunos/{id_aluno}/atividades/{atividade_id}/pdf")
async def baixar_atividade_adaptada_pdf(
    id_aluno: int,
    atividade_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa o arquivo .pdf de uma atividade adaptada gerada pela IA."""
    validar_permissao_chat_usuario(usuario_autenticado)
    item = _ATIVIDADES_PDF_MEMORIA.get(atividade_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo PDF de atividade adaptada não encontrado.",
        )
    dono_id, nome_arq, pdf_bytes = item
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    if dono_id != id_prof_token and usuario_autenticado.get("tipo_usuario") != "adm_sistema":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para baixar esta atividade.",
        )
    return Response(
        content=pdf_bytes,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{nome_arq}"',
            "X-Atividade-Id": atividade_id,
        },
    )


@router.get("/conversas", response_model=dict[str, list[ConversaResumo]])
async def get_conversas_lote(
    aluno_ids: Optional[str] = None,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, list[ConversaResumo]]:
    """Retorna conversas agrupadas por id_aluno em uma única requisição."""
    validar_permissao_chat_usuario(usuario_autenticado)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        agrupado = await listar_conversas_lote_db(aluno_ids=aluno_ids, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado="Alunos não encontrados.")
    return {
        str(k): [ConversaResumo(**c) for c in (v or [])]
        for k, v in (agrupado or {}).items()
    }


@router.get("/alunos/{id_aluno}/conversas", response_model=list[ConversaResumo])
async def get_conversas(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> list[ConversaResumo]:
    validar_permissao_chat_usuario(usuario_autenticado)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        lista = await listar_conversas_aluno_db(
            id_aluno, incluir_metricas=False, token=token_jwt
        )
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")
    return [ConversaResumo(**c) for c in (lista or [])]


@router.get("/alunos/{id_aluno}/conversas/{conversa_id}", response_model=ConversaDetalhe)
async def get_mensagens_conversa_endpoint(
    id_aluno: int,
    conversa_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ConversaDetalhe:
    validar_permissao_chat_usuario(usuario_autenticado)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        msgs = await obter_mensagens_conversa_db(id_aluno, conversa_id, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado="Conversa não encontrada.")
    return ConversaDetalhe(
        conversa_id=conversa_id,
        mensagens=[MensagemItem(**m) for m in msgs],
    )


@router.delete("/alunos/{id_aluno}/conversas/{conversa_id}")
async def delete_conversa_endpoint(
    id_aluno: int,
    conversa_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, Any]:
    """Remove uma conversa do professor no PostgreSQL via API-DB."""
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    _PEDIDOS_ATIVIDADE_PENDENTES.pop((id_prof_token, id_aluno, conversa_id), None)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        return await excluir_conversa_aluno_db(id_aluno, conversa_id, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado="Conversa não encontrada.")
