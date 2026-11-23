from __future__ import annotations

import base64
from datetime import datetime, timezone
import uuid
from typing import Any
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
from db_client import ResourceNotFoundError, obter_aluno
from guardrails import sanitizar_dados_sensiveis, validar_e_higienizar_entrada
from llm_client import gerar_resposta
from memoria.chroma_client import salvar_documento
from memoria.queries import (
    buscar_contexto_aluno,
    buscar_documentos_referencia,
    excluir_conversa_aluno,
    listar_conversas_aluno,
    listar_peis_aluno,
    obter_contexto_peis_aluno,
    obter_mensagens_conversa,
    obter_pei_por_id,
)
from schemas import (
    AnexoItem,
    ChatRequest,
    ChatResponse,
    ConversaDetalhe,
    ConversaResumo,
    MensagemItem,
)

router = APIRouter(tags=["chat"])

_CONVERSAS_SESSAO: dict[int, dict[str, dict[str, Any]]] = {}
_ATIVIDADES_DOCX_MEMORIA: dict[str, tuple[int, str, bytes]] = {}
_ATIVIDADES_PDF_MEMORIA: dict[str, tuple[int, str, bytes]] = {}

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
        aluno = await obter_aluno(id_aluno, token=usuario_autenticado.get("_raw_token"))
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

    convs_aluno = _CONVERSAS_SESSAO.setdefault(id_aluno, {})
    conv_mem_pre = convs_aluno.get(conversa_id) or {}
    pedido_pendente_pre = conv_mem_pre.get("pedido_atividade_pendente")
    texto_para_validacao = mensagem_base
    if textos_anexos or pedido_pendente_pre or payload.template_id or payload.modo_adaptacao:
        texto_para_validacao = f"{mensagem_base} (adaptação de atividade pedagógica para o aluno)"

    valido, _, erro_guardrail = validar_e_higienizar_entrada(texto_para_validacao)
    if not valido:
        return ChatResponse(resposta=erro_guardrail, conversa_id=conversa_id)
    msg_segura, _ = sanitizar_dados_sensiveis(mensagem_base)
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
    blocos_contexto_aluno: list[str] = []
    if perfil_contexto:
        perfil_contexto, _ = sanitizar_dados_sensiveis(perfil_contexto)
        blocos_contexto_aluno.append(f"PERFIL E CONTEXTO DO ALUNO:\n{perfil_contexto}")

    contexto_peis = obter_contexto_peis_aluno(id_aluno, limite_peis=2, max_chars_por_pei=2500)
    if contexto_peis:
        contexto_peis, _ = sanitizar_dados_sensiveis(contexto_peis)
        blocos_contexto_aluno.append(
            f"PEI(S) CRIADO(S) DO ALUNO (MEMÓRIA OFICIAL):\n{contexto_peis}"
        )

    memoria_relevante = buscar_contexto_aluno(id_aluno=id_aluno, query=msg_segura, n_results=3)
    if memoria_relevante:
        memoria_relevante, _ = sanitizar_dados_sensiveis(memoria_relevante)
        blocos_contexto_aluno.append(
            f"MEMÓRIA PEDAGÓGICA RECENTE DO ALUNO:\n{memoria_relevante[:1500]}"
        )

    contexto_combinado = "\n\n".join(blocos_contexto_aluno)

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

    conv_mem_existente = convs_aluno.get(conversa_id) or {}
    pedido_pendente = conv_mem_existente.get("pedido_atividade_pendente")
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

    # Caso 1: Professor pediu uma atividade na conversa (sem arquivo) e ainda não escolheu o template
    if pediu_atividade_na_conversa:
        opcoes_recomendadas = recomendar_templates_para_perfil(
            neurodivergencia=aluno.neurodiv_aluno,
            desc_aluno=desc_aluno_limpa,
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
        agora_iso = datetime.now(timezone.utc).isoformat()
        titulo_resumo, _ = sanitizar_dados_sensiveis(msg_segura[:40])
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
        conv_atual["pedido_atividade_pendente"] = {
            "tema": msg_segura,
            "opcoes": opcoes_recomendadas,
        }
        conv_atual["mensagens"].extend(
            [
                {"role": "user", "conteudo": msg_segura, "data": agora_iso, "anexos": None},
                {"role": "assistant", "conteudo": resposta_escolha, "data": agora_iso, "anexos": None},
            ]
        )
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
        if conv_mem_existente and "pedido_atividade_pendente" in conv_mem_existente:
            conv_mem_existente.pop("pedido_atividade_pendente", None)
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
        f"- Hiperfocos e Observações: {desc_aluno_limpa or 'Nenhuma'}\n\n"
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
        f"9. Memória e PEI do Aluno: Sempre consulte e respeite o(s) PEI(s) e a memória pedagógica do aluno fornecidos no contexto para responder com precisão sobre o que foi planejado para ele.\n"
        f"10. Veracidade de Recursos: JAMAIS invente títulos fictícios de vídeos do YouTube, livros, canais, autores ou links inexistentes; ao recomendar vídeos ou materiais, indique apenas o formato, a proposta pedagógica e termos de busca."
    )

    pediu_documento_pei = msg_segura.strip().lower() in ("documento", "gerar documento", "baixar pei", "enviar documento")
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
        from api.routes.pei import _PEI_DOCX_MEMORIA, montar_docx_pei

        peis_existentes = listar_peis_aluno(id_aluno)
        docx_pei_bytes: bytes | None = None
        pei_id_alvo = str(uuid.uuid4())
        if peis_existentes:
            pei_id_alvo = peis_existentes[0]["pei_id"]
            docx_pei_bytes = _PEI_DOCX_MEMORIA.get(pei_id_alvo)
            if not docx_pei_bytes:
                detalhe = obter_pei_por_id(id_aluno, pei_id_alvo)
                texto_pei = (detalhe or {}).get("documento_gerado", "")
                docx_pei_bytes = montar_docx_pei(
                    nome_aluno=nome_aluno_limpo,
                    respostas_gerais={1: texto_pei, 2: texto_pei, 3: texto_pei, 4: texto_pei},
                )
        else:
            docx_pei_bytes = montar_docx_pei(
                nome_aluno=nome_aluno_limpo,
                respostas_gerais={
                    1: f"Planejamento curricular adaptado para {nome_aluno_limpo} ({aluno.neurodiv_aluno}).",
                    2: "Estratégias DUA, apoio visual, mediação individualizada e recursos de acessibilidade.",
                    3: "Avaliação contínua, portfólio e observação sistematizada respeitando o ritmo do estudante.",
                    4: "Atividades práticas, jogos educativos, vídeos curtos e materiais concretos.",
                },
            )
            _PEI_DOCX_MEMORIA[pei_id_alvo] = docx_pei_bytes

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
        resposta_ia = gerar_resposta(
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
            {
                "role": "user",
                "conteudo": msg_segura,
                "data": agora_iso,
                "anexos": [a.model_dump() for a in anexos_recebidos] if anexos_recebidos else None,
            },
            {
                "role": "assistant",
                "conteudo": resposta_ia,
                "data": agora_iso,
                "anexos": [a.model_dump() for a in anexos_resposta] if anexos_resposta else None,
            },
        ]
    )

    try:
        id_turma = aluno.id_turma or 0
        salvar_documento(
            id_aluno=id_aluno,
            id_turma=id_turma,
            id_prof=id_prof_token,
            tipo="chat_user",
            conteudo=msg_segura,
            conversa_id=conversa_id,
            titulo=titulo_resumo,
            data_iso=agora_iso,
        )
        salvar_documento(
            id_aluno=id_aluno,
            id_turma=id_turma,
            id_prof=id_prof_token,
            tipo="chat_ia",
            conteudo=resposta_ia,
            conversa_id=conversa_id,
            titulo=titulo_resumo,
            data_iso=agora_iso,
        )
    except Exception:
        pass

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
        token=usuario_autenticado.get("_raw_token"),
    )

    return ChatResponse(
        resposta=resposta_ia,
        conversa_id=conversa_id,
        perfil_atualizado=perfil_atualizado,
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


@router.get("/alunos/{id_aluno}/conversas", response_model=list[ConversaResumo])
async def get_conversas(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> list[ConversaResumo]:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    conversas = listar_conversas_aluno(id_aluno, id_prof=id_prof_token)
    por_id = {c["conversa_id"]: c for c in conversas}
    for cid, c_mem in _CONVERSAS_SESSAO.get(id_aluno, {}).items():
        if c_mem.get("id_prof") == id_prof_token and cid not in por_id:
            por_id[cid] = {
                "conversa_id": cid,
                "titulo": c_mem.get("titulo", ""),
                "data": c_mem.get("data", ""),
            }
    lista = sorted(por_id.values(), key=lambda x: x.get("data", ""), reverse=True)
    return [ConversaResumo(**c) for c in lista]


@router.get("/alunos/{id_aluno}/conversas/{conversa_id}", response_model=ConversaDetalhe)
async def get_mensagens_conversa_endpoint(
    id_aluno: int,
    conversa_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> ConversaDetalhe:
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    conv_mem = _CONVERSAS_SESSAO.get(id_aluno, {}).get(conversa_id)
    if conv_mem and conv_mem.get("id_prof") is not None and conv_mem.get("id_prof") != id_prof_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta conversa pertence a outro professor.",
        )
    msgs = obter_mensagens_conversa(id_aluno, conversa_id, id_prof=id_prof_token)
    if not msgs and conv_mem and conv_mem.get("id_prof") == id_prof_token:
        msgs = list(conv_mem.get("mensagens", []))
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
    """Remove uma conversa da sessão e do banco vetorial ChromaDB."""
    validar_permissao_chat_usuario(usuario_autenticado)
    id_prof_token = int(usuario_autenticado.get("id_prof") or usuario_autenticado.get("sub") or 1)
    convs_aluno = _CONVERSAS_SESSAO.get(id_aluno, {})
    conv_mem = convs_aluno.get(conversa_id)
    if conv_mem and conv_mem.get("id_prof") is not None and conv_mem.get("id_prof") != id_prof_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta conversa pertence a outro professor.",
        )
    if conversa_id in convs_aluno:
        del convs_aluno[conversa_id]
    removidos = excluir_conversa_aluno(id_aluno, conversa_id, id_prof=id_prof_token)
    return {
        "mensagem": "Conversa removida com sucesso.",
        "conversa_id": conversa_id,
        "documentos_removidos": removidos,
    }
