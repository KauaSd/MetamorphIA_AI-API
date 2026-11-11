import base64
from datetime import datetime, timezone
import json
from typing import Optional, Union
import uuid
from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from api.routes.pei import (
    DOCX_MEDIA_TYPE,
    PERGUNTAS_GERAIS_MODELO,
    formatar_texto_pei_completo,
    gerar_respostas_gerais_pei,
    gerar_roteiro_pei_inicial,
    montar_docx_pei,
)
from config import get_settings
from guardrails import sanitizar_dados_sensiveis, validar_e_higienizar_entrada
from llm_client import gerar_resposta, gerar_resposta_stream
from memoria.queries import buscar_documentos_referencia
from schemas import PeiCriarResponse, PeiInicialResponse, PeiRespostaItem

router = APIRouter(prefix="/mock", tags=["mock"])


class MockChatRequest(BaseModel):
    mensagem: str


class SimularChatRequest(BaseModel):
    neurodivergencias: Union[list[str], str] = Field(
        default=["TEA (Nível 1)", "TDAH e Dislexia"],
        description="Lista de neurodivergências (cada posição representa um aluno diferente, pareado 1 para 1 com a pergunta de mesma posição em 'perguntas').",
        json_schema_extra={"example": ["TEA (Nível 1)", "TDAH e Dislexia"]},
    )
    perguntas: Optional[Union[list[str], str]] = Field(
        default=None,
        description="Lista de perguntas (cada pergunta[i] é feita diretamente para a neurodivergencias[i], como alunos diferentes). Se omitida, o mock simula uma pergunta específica para cada neurodivergência.",
        json_schema_extra={
            "example": [
                "Como posso adaptar uma atividade de frações em grupo para este aluno que tem sensibilidade a barulho e prefere rotina visual?",
                "Quais estratégias usar na aula de leitura quando este aluno se distrai rápido e trava em textos longos?",
            ]
        },
    )
    nome_aluno: Union[list[str], str] = Field(
        default="Aluno Simulado",
        description="Nome fictício (ou lista de nomes para cada aluno simulado)",
    )
    idade_aluno: Optional[int] = Field(default=10, description="Idade fictícia do aluno")
    observacoes_aluno: Optional[str] = Field(
        default=None,
        description="Observações ou hiperfocos opcionais do aluno",
    )


class PerguntaRespostaSimulada(BaseModel):
    pergunta: str = Field(..., description="Pergunta do professor para este aluno")
    resposta_ia: str = Field(..., description="Resposta gerada pela IA para o usuário")


class SimulacaoNeuroItem(BaseModel):
    aluno: str = Field(..., description="Identificação do aluno simulado")
    neurodivergencia: str = Field(..., description="Neurodivergência deste aluno")
    pergunta: str = Field(..., description="Pergunta direcionada especificamente a este aluno/neurodivergência")
    resposta_ia: str = Field(..., description="Resposta da IA para a pergunta deste aluno")
    respostas: list[PerguntaRespostaSimulada] = Field(
        ..., description="Lista detalhada de interações deste aluno"
    )


class SimularChatResponse(BaseModel):
    total_neurodivergencias_testadas: int
    perguntas_utilizadas: list[str]
    simulacoes: list[SimulacaoNeuroItem]


def _pergunta_padrao_por_neurodivergencia(neuro_limpa: str) -> str:
    """Retorna uma pergunta pedagógica simulada específica para a neurodivergência do aluno."""
    neuro_lower = neuro_limpa.lower()
    if "tea" in neuro_lower or "autis" in neuro_lower:
        return (
            f"Como posso adaptar uma atividade prática de frações para este aluno com {neuro_limpa}, "
            f"evitando sobrecarga sensorial e usando apoio visual?"
        )
    if "tdah" in neuro_lower and "dislexia" in neuro_lower:
        return (
            f"Como posso adaptar uma atividade de interpretação de texto para este aluno com {neuro_limpa} "
            f"sem que ele perca o foco ou se canse com textos longos?"
        )
    if "tdah" in neuro_lower:
        return (
            f"Quais estratégias práticas posso usar quando este aluno com {neuro_limpa} "
            f"se distrai em atividades longas em sala de aula?"
        )
    if "dislexia" in neuro_lower:
        return (
            f"Como posso adaptar uma atividade de leitura e escrita para este aluno com {neuro_limpa} "
            f"utilizando recursos multimodais do DUA?"
        )
    return (
        f"Como posso adaptar uma atividade curricular em sala de aula considerando as necessidades "
        f"específicas deste aluno com {neuro_limpa}?"
    )


def _simular_pergunta_individual(
    neuro_limpa: str,
    nome_limpo: str,
    idade_aluno: Optional[int],
    obs_limpa: str,
) -> str:
    """Gera uma pergunta realista de professor direcionada especificamente à neurodivergência informada."""
    settings = get_settings()
    if settings.LLM_PROVIDER.lower() != "mock":
        prompt_gerar_pergunta = (
            f"Você está simulando um professor em uma plataforma de educação inclusiva.\n"
            f"Crie exatamente 1 pergunta curta, prática e direta que um professor faria à IA "
            f"pedindo ajuda para adaptar uma atividade escolar para um aluno com o seguinte perfil:\n"
            f"- Nome: {nome_limpo}\n"
            f"- Idade: {idade_aluno or 10} anos\n"
            f"- Neurodivergência: {neuro_limpa}\n"
            f"- Observações: {obs_limpa or 'Nenhuma'}\n\n"
            f"Regras obrigatórias:\n"
            f"1. Retorne APENAS a pergunta em uma única linha, sem numeração, sem aspas e sem introduções.\n"
            f"2. A pergunta deve ser diretamente relacionada à condição ({neuro_limpa}) e citar adaptação de atividade ou estratégia em sala de aula."
        )
        texto_gerado = gerar_resposta(prompt=prompt_gerar_pergunta, aplicar_guardrails=False)
        for linha in texto_gerado.splitlines():
            limpa = linha.strip().lstrip("0123456789.-•* \"'")
            if "?" in limpa and len(limpa) > 15:
                return limpa

    return _pergunta_padrao_por_neurodivergencia(neuro_limpa)


@router.post("/chat")
async def mock_chat(payload: MockChatRequest):
    def event_stream():
        for chunk in gerar_resposta_stream(payload.mensagem):
            yield f"data: {json.dumps({'pergunta': payload.mensagem, 'resposta': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/simular", response_model=SimularChatResponse)
async def simular_neurodivergencias_e_perguntas(payload: SimularChatRequest) -> SimularChatResponse:
    """Simula alunos diferentes onde cada neurodivergência[i] está diretamente relacionada à sua respectiva pergunta[i]."""
    lista_neuro = (
        [payload.neurodivergencias]
        if isinstance(payload.neurodivergencias, str)
        else list(payload.neurodivergencias)
    )

    if isinstance(payload.perguntas, str):
        lista_perguntas_bruta = [payload.perguntas]
    elif isinstance(payload.perguntas, list):
        lista_perguntas_bruta = list(payload.perguntas)
    else:
        lista_perguntas_bruta = []

    perguntas_enviadas = [
        p.strip()
        for p in lista_perguntas_bruta
        if p and p.strip() and p.strip().lower() != "string"
    ]

    obs_input = payload.observacoes_aluno
    if obs_input and obs_input.strip().lower() == "string":
        obs_input = None
    obs_limpa, _ = sanitizar_dados_sensiveis(obs_input or "")

    resultados: list[SimulacaoNeuroItem] = []
    todas_perguntas: list[str] = []

    for idx, neuro in enumerate(lista_neuro):
        neuro_limpa, _ = sanitizar_dados_sensiveis(neuro)

        # Define o nome individual deste aluno simulado
        if isinstance(payload.nome_aluno, list) and idx < len(payload.nome_aluno):
            nome_bruto = payload.nome_aluno[idx]
        elif len(lista_neuro) > 1 and payload.nome_aluno == "Aluno Simulado":
            nome_bruto = f"Aluno Simulado {idx + 1}"
        else:
            nome_bruto = (
                payload.nome_aluno
                if isinstance(payload.nome_aluno, str)
                else "Aluno Simulado"
            )
        nome_limpo, _ = sanitizar_dados_sensiveis(nome_bruto)

        # Relaciona diretamente a neurodivergência[idx] com a pergunta[idx] correspondente
        if idx < len(perguntas_enviadas):
            perguntas_deste_aluno = [perguntas_enviadas[idx]]
            # Se houver perguntas extras além do número de neurodivergências, distribui ao último aluno
            if idx == len(lista_neuro) - 1 and len(perguntas_enviadas) > len(lista_neuro):
                perguntas_deste_aluno.extend(perguntas_enviadas[idx + 1 :])
        else:
            perguntas_deste_aluno = [
                _simular_pergunta_individual(
                    neuro_limpa=neuro_limpa,
                    nome_limpo=nome_limpo,
                    idade_aluno=payload.idade_aluno,
                    obs_limpa=obs_limpa,
                )
            ]

        respostas_deste_aluno: list[PerguntaRespostaSimulada] = []

        for pergunta_bruta in perguntas_deste_aluno:
            valido, msg_segura, erro_guardrail = validar_e_higienizar_entrada(pergunta_bruta)
            if not valido:
                todas_perguntas.append(pergunta_bruta)
                respostas_deste_aluno.append(
                    PerguntaRespostaSimulada(pergunta=pergunta_bruta, resposta_ia=erro_guardrail)
                )
                continue

            todas_perguntas.append(msg_segura)

            fontes_docs = buscar_documentos_referencia(
                query=f"{neuro_limpa} {msg_segura}", n_results=2
            )

            prompt = (
                f"Você é o assistente pedagógico parceiro da MetamorphIA, conversando amigavelmente com o professor sobre o estudante:\n"
                f"- Nome: {nome_limpo}\n"
                f"- Idade: {payload.idade_aluno or 'Não informada'}\n"
                f"- Diagnóstico / Condição: {neuro_limpa}\n"
                f"- Hiperfocos e Observações: {obs_limpa or 'Nenhuma'}\n\n"
                f"Mensagem Atual do Professor:\n{msg_segura}\n\n"
                f"Diretrizes para a sua resposta:\n"
                f"1. Seja simpático, acolhedor e próximo, como um verdadeiro parceiro/professor assistente.\n"
                f"2. Dê uma breve saudação inicial calorosa e acolhedora ao professor e apresente propostas práticas, simples e criativas para a atividade do aluno.\n"
                f"3. Autoralidade Pedagógica: Transforme as diretrizes e metodologias (DUA, WWC, etc.) em ideias pedagógicas autorais e originais criadas sob medida para o aluno, sem copiar trechos brutos dos documentos.\n"
                f"4. Citação Orgânica ou Linha Discreta: Cite a fonte de apoio de forma orgânica no meio do texto ou em apenas 1 linha discreta em itálico no final: '*Fonte base: [nome da fonte]*'.\n"
                f"5. Fim do Rodapé Fixo: NUNCA adicione blocos fixos ou listas burocráticas de referências ao final da resposta.\n"
                f"6. Proponha ideias práticas e viáveis para a sala de aula real, com linguagem simples e acessível.\n"
                f"7. Limites Éticos e Clínicos: Se a mensagem envolver diagnóstico clínico (DSM-5/CID), dose/uso de medicamentos, afirmação de diagnóstico sem laudo para a família ou descrença na capacidade de aprender do estudante, responda com empatia e firmeza ética: esclareça que diagnóstico e medicação cabem exclusivamente à equipe médica/saúde, oriente diálogo acolhedor com a família baseado em observações pedagógicas e reafirme que todo estudante pode aprender com adaptações adequadas."
            )

            resposta_ia = gerar_resposta(
                prompt=prompt,
                fontes_referencia=fontes_docs,
                formato_referencias="discreto",
            )

            respostas_deste_aluno.append(
                PerguntaRespostaSimulada(pergunta=msg_segura, resposta_ia=resposta_ia)
            )

        primeira_interacao = respostas_deste_aluno[0]
        resultados.append(
            SimulacaoNeuroItem(
                aluno=nome_limpo,
                neurodivergencia=neuro_limpa,
                pergunta=primeira_interacao.pergunta,
                resposta_ia=primeira_interacao.resposta_ia,
                respostas=respostas_deste_aluno,
            )
        )

    return SimularChatResponse(
        total_neurodivergencias_testadas=len(resultados),
        perguntas_utilizadas=todas_perguntas,
        simulacoes=resultados,
    )


# Armazena em memória o contexto informado no /mock/pei/inicial para reaproveitar nos testes seguintes
_CONTEXTO_MOCK_SESSAO: dict[int, str] = {}


class MockPeiInicialRequest(BaseModel):
    id_aluno: int = Field(default=1, description="ID fictício do aluno")
    nome_aluno: str = Field(default="Lucas Oliveira", description="Nome fictício do aluno")
    neurodivergencia: str = Field(
        default="TEA (Nível 1) e TDAH",
        description="Neurodivergência do aluno simulado",
    )
    tem_contexto: Optional[bool] = Field(
        default=None,
        description="Opcional. Se omitido/False e não houver descricao_aluno, pede a pergunta inicial das 5 linhas.",
    )
    descricao_aluno: Optional[str] = Field(
        default=None,
        description="Descrição do estudante em pelo menos 5 linhas (preenchida pelo professor em resposta à pergunta inicial).",
    )
    componente_curricular: str = Field(default="Matemática", description="Componente curricular")
    ano_escolar: str = Field(default="5º Ano do Ensino Fundamental", description="Ano/série escolar")
    periodo_bimestre: int = Field(default=1, description="Bimestre (1 a 4)")
    indice_subpergunta: Optional[int] = Field(
        default=None,
        description="Índice opcional da subpergunta atual (0 a 7) para navegação passo a passo",
    )


class MockPeiCriarRequest(BaseModel):
    id_aluno: int = Field(default=1, description="ID fictício do estudante")
    nome_aluno: str = Field(default="Lucas Oliveira", description="Nome fictício do estudante")
    idade_aluno: Optional[int] = Field(default=10, description="Idade fictícia do estudante")
    neurodivergencia: str = Field(default="TEA (Nível 1) e TDAH", description="Neurodivergência")
    contexto_aluno: Optional[str] = Field(
        default=None,
        description="Contexto pedagógico do estudante (se omitido, usa a descrição enviada em /mock/pei/inicial)",
    )
    nome_professor_regente: str = Field(
        default="Prof.ª Ana Paula Souza", description="Nome do Professor Regente"
    )
    nome_professor_especializado: str = Field(
        default="Prof. Marcos Vinícius (AEE)",
        description="Nome do Professor Especializado da Educação Especial",
    )
    componente_curricular: str = Field(default="Matemática", description="Componente Curricular")
    ano_escolar: str = Field(default="5º Ano do Ensino Fundamental", description="Ano/série")
    periodo_bimestre: int = Field(default=1, description="Bimestre (1 a 4)")
    respostas: Optional[list[PeiRespostaItem]] = Field(
        default=None,
        description="Respostas das subperguntas do Roteiro MetamorphIA. Se omitidas, o mock usa respostas simuladas completas para todas as subperguntas.",
    )


def _respostas_subperguntas_mock_padrao() -> list[PeiRespostaItem]:
    """Retorna respostas simuladas para todas as subperguntas do Roteiro MetamorphIA."""
    return [
        PeiRespostaItem(
            numero_pergunta_geral=1,
            pergunta="O que o estudante já consegue fazer?",
            resposta="Compreende operações básicas com material concreto e resolve situações-problema curtas com apoio visual.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=1,
            pergunta="O que precisa e é possível de ser ajustado esse bimestre?",
            resposta="Simplificar ações, dividir a habilidade de frações em pequenas etapas e aumentar o tempo de trabalho sem retirar o conteúdo curricular.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=1,
            pergunta="E como isso será feito?",
            resposta="Com uso de recursos de acessibilidade, adaptações de materiais e linguagem e articulação com o professor especializado do AEE.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=2,
            pergunta="Quais estratégias pedagógicas você utilizará para que o estudante consiga participar das aulas?",
            resposta="Explicar o conteúdo em partes menores, garantir previsibilidade com rotina visual e dar instruções claras com apoio visual.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=2,
            pergunta="Quais intervenções pedagógicas você utilizará para que o estudante consiga participar das aulas?",
            resposta="Apoio individual durante atividades mais complexas, adaptação de quantidade e ajuste da forma de participação (oral, pictograma e digital).",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=2,
            pergunta="Quais recursos de acessibilidade você utilizará para que o estudante consiga participar das aulas?",
            resposta="Material ampliado com fontes acessíveis, figuras/esquemas visuais, uso de tablet e abafadores de ruído quando necessário.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=3,
            pergunta="Como você vai avaliar o estudante respeitando seu ritmo, suas necessidades e suas formas de expressão?",
            resposta="Observação diária, portfólio de atividades, avaliações práticas, checklist de habilidades e autoavaliação com apoio visual.",
        ),
        PeiRespostaItem(
            numero_pergunta_geral=4,
            pergunta="Quais vídeos, livros, jogos, exercícios ou outras atividades você indicará para apoiar e fortalecer o aprendizado do estudante?",
            resposta="Animações curtas com passo a passo visual, livros paradidáticos ilustrados, jogos de memória e associação de frações e atividades manipulativas com blocos.",
        ),
    ]


@router.post("/pei/inicial", response_model=PeiInicialResponse)
async def mock_pei_inicial(
    payload: Optional[MockPeiInicialRequest] = None,
) -> PeiInicialResponse:
    """Mock de teste para a rota de criação de PEI inicial (Roteiro MetamorphIA.docx).

    - Se descricao_aluno não for enviada (e não houver contexto salvo na sessão),
      retorna a pergunta inicial pedindo pelo menos 5 linhas sobre o aluno.
    - Quando descricao_aluno é enviada, salva na sessão de mock e a IA gera as 4 opções de cada subpergunta.
    """
    req = payload or MockPeiInicialRequest()
    desc_enviada = (req.descricao_aluno or "").strip()
    if desc_enviada.lower() in ("string", "none", "null"):
        desc_enviada = ""

    if desc_enviada:
        desc_limpa, _ = sanitizar_dados_sensiveis(desc_enviada)
        _CONTEXTO_MOCK_SESSAO[req.id_aluno] = desc_limpa
        contexto_final = desc_limpa
    elif req.tem_contexto is False:
        contexto_final = ""
    elif req.tem_contexto is True:
        contexto_final = _CONTEXTO_MOCK_SESSAO.get(req.id_aluno) or (
            f"Estudante {req.nome_aluno} com {req.neurodivergencia}, aprende melhor com apoio visual e etapas curtas."
        )
    else:
        contexto_final = _CONTEXTO_MOCK_SESSAO.get(req.id_aluno, "")

    nome_limpo, _ = sanitizar_dados_sensiveis(req.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(req.neurodivergencia)

    return gerar_roteiro_pei_inicial(
        id_aluno=req.id_aluno,
        nome_aluno=nome_limpo,
        neurodivergencia=neuro_limpa,
        contexto_aluno=contexto_final,
        componente_curricular=req.componente_curricular,
        ano_escolar=req.ano_escolar,
        periodo_bimestre=req.periodo_bimestre,
        indice_subpergunta=req.indice_subpergunta,
    )


def _executar_mock_pei(payload: Optional[MockPeiCriarRequest] = None) -> tuple[PeiCriarResponse, bytes]:
    """Executa a geração do PEI mockado via IA e constrói o arquivo .docx sem as partes azuis."""
    req = payload or MockPeiCriarRequest()
    nome_limpo, _ = sanitizar_dados_sensiveis(req.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(req.neurodivergencia)
    ctx_bruto = req.contexto_aluno
    if not ctx_bruto or ctx_bruto.strip().lower() in ("string", "none", "null"):
        ctx_bruto = _CONTEXTO_MOCK_SESSAO.get(req.id_aluno, "")
    contexto_limpo, _ = sanitizar_dados_sensiveis(ctx_bruto)
    prof_regente, _ = sanitizar_dados_sensiveis(req.nome_professor_regente)
    prof_esp, _ = sanitizar_dados_sensiveis(req.nome_professor_especializado)
    componente, _ = sanitizar_dados_sensiveis(req.componente_curricular)
    ano_escolar, _ = sanitizar_dados_sensiveis(req.ano_escolar)
    bimestre = req.periodo_bimestre if req.periodo_bimestre in (1, 2, 3, 4) else 1

    respostas_subs = req.respostas or _respostas_subperguntas_mock_padrao()

    fontes_docs = buscar_documentos_referencia(
        query=f"PEI {neuro_limpa} {componente} adaptações curriculares acessibilidade DUA",
        n_results=2,
    )
    textos_rag = "\n\n".join(f"[{f['titulo']}]: {f['texto']}" for f in fontes_docs)

    respostas_gerais_int = gerar_respostas_gerais_pei(
        nome_aluno=nome_limpo,
        idade_aluno=req.idade_aluno,
        neurodivergencia=neuro_limpa,
        contexto_aluno=contexto_limpo,
        respostas_subperguntas=respostas_subs,
        componente_curricular=componente,
        ano_escolar=ano_escolar,
        periodo_bimestre=bimestre,
        contexto_rag=textos_rag,
        fontes_docs=fontes_docs,
    )

    documento_gerado = formatar_texto_pei_completo(
        nome_aluno=nome_limpo,
        nome_professor_regente=prof_regente,
        nome_professor_especializado=prof_esp,
        componente_curricular=componente,
        periodo_bimestre=bimestre,
        respostas_gerais=respostas_gerais_int,
    )

    docx_bytes = montar_docx_pei(
        nome_aluno=nome_limpo,
        respostas_gerais=respostas_gerais_int,
        nome_professor_regente=prof_regente,
        nome_professor_especializado=prof_esp,
        componente_curricular=componente,
        periodo_bimestre=bimestre,
    )

    pei_id = f"mock-pei-{uuid.uuid4()}"
    data_iso = datetime.now(timezone.utc).isoformat()
    nome_arq = f"PEI_Mock_{bimestre}Bim_2026.docx"

    resp_obj = PeiCriarResponse(
        pei_id=pei_id,
        documento_gerado=documento_gerado,
        data=data_iso,
        formato="docx",
        arquivo_nome=nome_arq,
        respostas_gerais={
            PERGUNTAS_GERAIS_MODELO[k]: v for k, v in respostas_gerais_int.items()
        },
        documento_docx_base64=base64.b64encode(docx_bytes).decode("ascii"),
        download_url="/mock/pei/docx",
    )
    return resp_obj, docx_bytes


@router.post("/pei", response_model=PeiCriarResponse)
async def mock_criar_pei(
    payload: Optional[MockPeiCriarRequest] = None,
    formato: str = Query(
        default="json",
        description="Use 'docx' para baixar diretamente o arquivo .docx ou 'json' para receber os metadados e o .docx em base64.",
    ),
):
    """Mock de teste para criação do PEI completo (com geração do .docx no modelo 2026)."""
    resp_obj, docx_bytes = _executar_mock_pei(payload)
    if formato.lower() == "docx":
        return Response(
            content=docx_bytes,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{resp_obj.arquivo_nome}"',
                "X-Pei-Id": resp_obj.pei_id,
            },
        )
    return resp_obj


@router.post("/pei/docx")
async def mock_criar_pei_docx(
    payload: Optional[MockPeiCriarRequest] = None,
) -> Response:
    """Mock de teste que retorna diretamente o arquivo binário .docx do PEI gerado."""
    resp_obj, docx_bytes = _executar_mock_pei(payload)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{resp_obj.arquivo_nome}"',
            "X-Pei-Id": resp_obj.pei_id,
        },
    )
