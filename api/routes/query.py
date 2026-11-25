from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

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
from auth_jwt import verificar_token_jwt
from cache_utils import BoundedTTLCache
from guardrails import (
    MENSAGEM_ALERTA_DADOS_SENSIVEIS,
    detectar_prompt_injection,
    sanitizar_dados_sensiveis,
)
from llm_client import gerar_resposta_async
from memoria.queries import buscar_documentos_referencia
from api.schemas.response import AlunoResponse
from schemas import AnexoItem

Queryrouter = APIRouter(prefix="/query", tags=["query"])

_QUERY_ATIVIDADES_DOCX: BoundedTTLCache[str, tuple[int, str, bytes]] = BoundedTTLCache(ttl_seconds=3600, max_items=100)
_QUERY_ATIVIDADES_PDF: BoundedTTLCache[str, tuple[int, str, bytes]] = BoundedTTLCache(ttl_seconds=3600, max_items=100)


class AdaptarAtividadeRequest(BaseModel):
    atividade: str = Field(default="", max_length=5000, description="Descrição ou enunciado da atividade original a ser adaptada")
    neurodivergencia: str = Field(..., min_length=1, max_length=300, description="Condição do estudante (ex: TEA, TDAH, Dislexia, TOD)")
    ano_escolar: Optional[str] = Field(None, max_length=150, description="Série ou ano escolar do aluno (ex: 5º ano fundamental)")
    objetivo_pedagogico: Optional[str] = Field(None, max_length=1000, description="Objetivo principal da aprendizagem")
    nome_aluno: Optional[str] = Field(None, max_length=150, description="Nome do estudante para cabeçalho do documento")
    arquivo_nome: Optional[str] = Field(None, max_length=255, description="Nome do arquivo enviado pelo professor")
    arquivo_base64: Optional[str] = Field(None, description="Conteúdo do arquivo enviado em base64 ou Data URL")
    anexos: Optional[list[AnexoItem]] = Field(default=None, description="Lista de arquivos anexados pelo professor")
    modo_adaptacao: Optional[str] = Field(default=None, description="fiel | template")
    template_id: Optional[str] = Field(default=None, description="ID do template pedagógico")
    formato_saida: Optional[str] = Field(default="docx", description="docx | pdf")
    instrucoes_ia: Optional[str] = Field(default=None, max_length=3000)


class AdaptarAtividadeResponse(BaseModel):
    atividade_original: str
    neurodivergencia: str
    adaptacoes_sugeridas: str
    recursos_recomendados: list[str]
    atividade_id: Optional[str] = None
    arquivo_nome: Optional[str] = None
    documento_docx_base64: Optional[str] = None
    documento_pdf_base64: Optional[str] = None
    formato_arquivo: Optional[str] = "docx"
    modo_adaptacao_usado: Optional[str] = None
    template_usado: Optional[str] = None
    data_url: Optional[str] = None
    download_url: Optional[str] = None
    anexo: Optional[AnexoItem] = None


class PerguntaRequest(BaseModel):
    pergunta: str = Field(..., min_length=1, max_length=5000, description="Dúvida pedagógica ou educacional inclusiva")
    instrucoes_ia: Optional[str] = Field(default=None, max_length=3000)


@Queryrouter.post("/adaptar-atividade", response_model=AdaptarAtividadeResponse)
async def adaptar_atividade_endpoint(
    payload: AdaptarAtividadeRequest,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AdaptarAtividadeResponse:
    textos_extraidos: list[str] = []
    nome_arq_origem = payload.arquivo_nome or ""

    if payload.arquivo_base64:
        raw_bytes = decodificar_base64_ou_data_url(payload.arquivo_base64)
        extraido = extrair_texto_arquivo(nome_arq_origem or "atividade.pdf", raw_bytes)
        if extraido:
            textos_extraidos.append(extraido[:4500])

    for anexo in payload.anexos or []:
        if not nome_arq_origem:
            nome_arq_origem = anexo.name
        if anexo.dataUrl:
            raw_bytes = decodificar_base64_ou_data_url(anexo.dataUrl)
            extraido = extrair_texto_arquivo(anexo.name, raw_bytes, anexo.type or "")
            if extraido:
                textos_extraidos.append(f"[{anexo.name}]\n{extraido[:4500]}")

    atividade_bruta = (payload.atividade or "").strip()
    if textos_extraidos:
        bloco_extraido = "\n\n".join(textos_extraidos)
        atividade_bruta = f"{atividade_bruta}\n\n{bloco_extraido}".strip() if atividade_bruta else bloco_extraido

    if not atividade_bruta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Informe a descrição da atividade ou envie um arquivo para adaptação.",
        )

    texto_combinado = " ".join(
        filter(None, [atividade_bruta, payload.neurodivergencia, payload.ano_escolar, payload.objetivo_pedagogico])
    )
    eh_ataque, msg_inj = detectar_prompt_injection(texto_combinado)
    if eh_ataque:
        return AdaptarAtividadeResponse(
            atividade_original=atividade_bruta,
            neurodivergencia=payload.neurodivergencia,
            adaptacoes_sugeridas=msg_inj,
            recursos_recomendados=[],
        )

    atividade_limpa, pii_ativ = sanitizar_dados_sensiveis(atividade_bruta)
    neurodiv_limpa, pii_neuro = sanitizar_dados_sensiveis(payload.neurodivergencia)
    ano_limpo, pii_ano = sanitizar_dados_sensiveis(payload.ano_escolar or "")
    obj_limpo, pii_obj = sanitizar_dados_sensiveis(payload.objetivo_pedagogico or "")
    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(payload.nome_aluno or "Estudante")

    if pii_ativ or pii_neuro or pii_ano or pii_obj:
        return AdaptarAtividadeResponse(
            atividade_original=atividade_limpa,
            neurodivergencia=neurodiv_limpa,
            adaptacoes_sugeridas=MENSAGEM_ALERTA_DADOS_SENSIVEIS,
            recursos_recomendados=[],
        )

    termo_busca = f"adaptação atividade {neurodiv_limpa} {atividade_limpa[:80]}"
    fontes = buscar_documentos_referencia(termo_busca, n_results=3)

    from templates_atividades import obter_template_por_id

    tpl = obter_template_por_id(payload.template_id)
    tem_arquivo = bool(textos_extraidos)
    modo_usado = "fiel" if (tem_arquivo and not tpl) else ("template" if tpl else (payload.modo_adaptacao or "fiel"))

    bloco_modo = ""
    if tpl:
        bloco_modo = f"\n{tpl['instrucao_prompt']}\n"
    elif tem_arquivo:
        bloco_modo = (
            "\nMODO FIEL AO ARQUIVO ORIGINAL: Mantenha a estrutura e todas as questões originais do arquivo enviado, "
            "adaptando a linguagem, o fracionamento dos enunciados e os apoios visuais/concretos.\n"
        )

    bloco_inst = ""
    if payload.instrucoes_ia and payload.instrucoes_ia.strip():
        inst_limpa, _ = sanitizar_dados_sensiveis(payload.instrucoes_ia.strip())
        bloco_inst = f"\nINSTRUÇÕES PERSONALIZADAS DO PROFESSOR:\n{inst_limpa[:2000]}\n"

    prompt = f"""
Você é um especialista em Desenho Universal para a Aprendizagem (DUA 3.0) e Educação Inclusiva.
Adapte a seguinte atividade para um estudante neurodivergente:
- Condição / Diagnóstico: {neurodiv_limpa}
- Ano / Faixa Escolar: {ano_limpo or 'Não especificado'}
- Objetivo Pedagógico: {obj_limpo or 'Garantir a compreensão e participação ativa'}
{bloco_inst}{bloco_modo}
Atividade Original do Professor:
\"\"\"{atividade_limpa}\"\"\"

ESTRUTURE SUA RESPOSTA EM:
1. ANÁLISE DE BARREIRAS POTENCIAIS NA ATIVIDADE ORIGINAL
2. PROPOSTA DE ATIVIDADE ADAPTADA (Passo a passo com linguagem clara)
3. PRINCÍPIOS DUA APLICADOS (Múltiplos meios de Engajamento, Representação e Expressão)
4. RECURSOS COMPLEMENTARES E ESTRATÉGIAS DE MEDIAÇÃO
"""

    resposta_texto = await gerar_resposta_async(
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

    atividade_id = str(uuid.uuid4())
    titulo_doc = nome_arq_origem.rsplit(".", 1)[0] if nome_arq_origem else atividade_limpa[:50]
    docx_bytes = montar_docx_atividade_adaptada(
        titulo=titulo_doc,
        nome_aluno=nome_aluno_limpo,
        neurodivergencia=neurodiv_limpa,
        conteudo_adaptado=resposta_texto,
        ano_escolar=ano_limpo or None,
    )
    pdf_bytes = montar_pdf_atividade_adaptada(
        titulo=titulo_doc,
        nome_aluno=nome_aluno_limpo,
        neurodivergencia=neurodiv_limpa,
        conteudo_adaptado=resposta_texto,
        ano_escolar=ano_limpo or None,
    )
    nome_base = "".join(c if c.isalnum() else "_" for c in (titulo_doc or "Atividade")).strip("_")[:40] or "Atividade"
    nome_docx = f"Atividade_Adaptada_{nome_base}.docx"
    nome_pdf = f"Atividade_Adaptada_{nome_base}.pdf"
    id_prof_raw = _usuario_autenticado.get("id_prof")
    if id_prof_raw is None:
        id_prof_raw = _usuario_autenticado["sub"]
    id_prof_token = int(id_prof_raw)
    _QUERY_ATIVIDADES_DOCX[atividade_id] = (id_prof_token, nome_docx, docx_bytes)
    _QUERY_ATIVIDADES_PDF[atividade_id] = (id_prof_token, nome_pdf, pdf_bytes)
    docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    formato_escolhido = (payload.formato_saida or "docx").strip().lower()
    if formato_escolhido == "pdf":
        arquivo_nome_saida = nome_pdf
        data_url = para_data_url_pdf(pdf_bytes)
        dl_url = f"/query/adaptar-atividade/{atividade_id}/pdf"
        anexo_resp = AnexoItem(
            name=nome_pdf,
            type=PDF_MEDIA_TYPE,
            size=len(pdf_bytes),
            dataUrl=data_url,
        )
    else:
        formato_escolhido = "docx"
        arquivo_nome_saida = nome_docx
        data_url = para_data_url_docx(docx_bytes)
        dl_url = f"/query/adaptar-atividade/{atividade_id}/docx"
        anexo_resp = AnexoItem(
            name=nome_docx,
            type=DOCX_MEDIA_TYPE,
            size=len(docx_bytes),
            dataUrl=data_url,
        )

    return AdaptarAtividadeResponse(
        atividade_original=atividade_limpa,
        neurodivergencia=neurodiv_limpa,
        adaptacoes_sugeridas=resposta_texto,
        recursos_recomendados=recursos,
        atividade_id=atividade_id,
        arquivo_nome=arquivo_nome_saida,
        documento_docx_base64=docx_b64,
        documento_pdf_base64=pdf_b64,
        formato_arquivo=formato_escolhido,
        modo_adaptacao_usado=modo_usado,
        template_usado=tpl["id"] if tpl else None,
        data_url=data_url,
        download_url=dl_url,
        anexo=anexo_resp,
    )


@Queryrouter.post("/adaptar-atividade/arquivo", response_model=AdaptarAtividadeResponse)
async def adaptar_atividade_arquivo_endpoint(
    payload: AdaptarAtividadeRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AdaptarAtividadeResponse:
    """Recebe um arquivo (em base64/Data URL) e retorna a atividade adaptada + arquivo .docx pronto para download."""
    return await adaptar_atividade_endpoint(payload=payload, _usuario_autenticado=usuario_autenticado)


@Queryrouter.get("/adaptar-atividade/{atividade_id}/docx")
async def baixar_query_atividade_docx(
    atividade_id: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa diretamente o arquivo .docx de uma atividade adaptada via /query."""
    item = _QUERY_ATIVIDADES_DOCX.get(atividade_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo de atividade adaptada não encontrado.",
        )
    dono_id, nome_arq, docx_bytes = item
    id_prof_token = int(_usuario_autenticado.get("id_prof") or _usuario_autenticado.get("sub") or 0)
    if dono_id != id_prof_token and _usuario_autenticado.get("tipo_usuario") != "adm_sistema":
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


@Queryrouter.get("/adaptar-atividade/{atividade_id}/pdf")
async def baixar_query_atividade_pdf(
    atividade_id: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa diretamente o arquivo .pdf de uma atividade adaptada via /query."""
    item = _QUERY_ATIVIDADES_PDF.get(atividade_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo PDF de atividade adaptada não encontrado.",
        )
    dono_id, nome_arq, pdf_bytes = item
    id_prof_token = int(_usuario_autenticado.get("id_prof") or _usuario_autenticado.get("sub") or 0)
    if dono_id != id_prof_token and _usuario_autenticado.get("tipo_usuario") != "adm_sistema":
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


@Queryrouter.post("/perguntar")
async def perguntar_ia_post(
    payload: PerguntaRequest,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, str]:
    fontes = buscar_documentos_referencia(payload.pergunta, n_results=3)
    prompt_pergunta = payload.pergunta
    if payload.instrucoes_ia and payload.instrucoes_ia.strip():
        inst_limpa, _ = sanitizar_dados_sensiveis(payload.instrucoes_ia.strip())
        prompt_pergunta = (
            f"INSTRUÇÕES PERSONALIZADAS DO PROFESSOR:\n{inst_limpa[:2000]}\n\n"
            f"Pergunta do Professor:\n{payload.pergunta}"
        )
    resposta = await gerar_resposta_async(prompt_pergunta, fontes_referencia=fontes, formato_referencias="discreto")
    return {"resposta": resposta}


@Queryrouter.get("/enviaTxtIA/{pergunta}")
async def enviaTxtIA(
    pergunta: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> dict[str, str]:
    fontes = buscar_documentos_referencia(pergunta, n_results=2)
    return {"resposta": await gerar_resposta_async(pergunta, fontes_referencia=fontes)}


@Queryrouter.get("/Envia/{DescAl}", response_model=AlunoResponse)
async def enviaInfoAluno(
    DescAl: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> AlunoResponse:
    eh_ataque, msg_inj = detectar_prompt_injection(DescAl)
    if eh_ataque:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg_inj,
        )
    desc_limpa, _ = sanitizar_dados_sensiveis(DescAl)

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
{desc_limpa}

Exemplo de saída esperada:
{{"nome":"João Silva","idade":10,"serie":"5º ano","neurodivergencia":"TDAH","diagnosticado":true,"descricao":"Aluno comunicativo..."}}
"""
    result = await gerar_resposta_async(prompt, aplicar_guardrails=False)

    match = re.search(r"\{.*\}", result, re.DOTALL)
    if match:
        try:
            dados = json.loads(match.group(0))
            return AlunoResponse.model_validate(dados)
        except Exception:
            pass

    # Fallback determinístico quando o provedor estiver em modo mock ou retornar texto livre
    idade_match = re.search(r"\b(\d{1,2})\s*anos?\b", desc_limpa, re.IGNORECASE)
    serie_match = re.search(r"\b(\d+[º°o]?\s*ano(?:\s+do\s+ensino\s+\w+)?)\b", desc_limpa, re.IGNORECASE)
    neuro_match = re.search(r"\b(TEA|TDAH|Dislexia|Discalculia|TOD|Autismo)\b", desc_limpa, re.IGNORECASE)
    tem_suspeita = bool(re.search(r"\bsuspeita\b", desc_limpa, re.IGNORECASE))
    return AlunoResponse(
        nome="",
        idade=int(idade_match.group(1)) if idade_match else None,
        serie=serie_match.group(1) if serie_match else "",
        neurodivergencia=neuro_match.group(1).upper() if neuro_match else "",
        diagnosticado=(False if tem_suspeita else True) if neuro_match else None,
        descricao=desc_limpa,
    )
