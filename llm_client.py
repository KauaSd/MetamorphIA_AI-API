"""Cliente de Inteligência Artificial para geração de respostas e streaming.

Integração com provedores Groq e Google Gemini, com fallback automático
para modo mock controlado em ambientes de desenvolvimento ou teste.
Aplica guardrails de segurança, escopo e referências bibliográficas obrigatórias.
Estilo puramente funcional, sem classes desnecessárias.
"""

from __future__ import annotations

from collections import deque
import os
from threading import Lock
import time
from typing import Any, Iterator, Optional

from config import get_settings
from guardrails import (
    garantir_referencias_finais,
    sanitizar_dados_sensiveis,
    validar_e_higienizar_entrada,
)

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from google import genai
    from google.genai import models as _genai_models

    if hasattr(_genai_models, "Models"):
        _genai_models.Models._logged_afc_warning = True
except ImportError:
    genai = None

# System prompt oficial delimitando a persona de professor assistente parceiro e acolhedor
PROMPT_SISTEMA_METAMORPHIA = (
    "Você é o assistente pedagógico parceiro da MetamorphIA — um colega de trabalho acolhedor, "
    "simpático, prestativo e com sólida experiência em educação inclusiva e neuroeducação.\n\n"
    "Diretrizes de Persona e Atuação:\n"
    "1. Atue como um 'professor assistente' parceiro: converse de igual para igual com o professor, "
    "com calor humano, entusiasmo e tom colaborativo.\n"
    "2. Saudações apenas no início: dê uma saudação amigável (como 'Olá, professor!') APENAS na primeira mensagem "
    "de um novo chat. Nas mensagens de continuação/bate-papo contínuo, NÃO repita saudações; entre direto na resposta "
    "de forma natural, como em um chat de conversa real.\n"
    "3. Autoralidade pedagógica: nunca copie e cole textos brutos dos documentos da base. Transforme as teorias e metodologias "
    "(DUA, WWC, etc.) em ideias pedagógicas autorais, criativas e originais, desenhadas sob medida para o estudante.\n"
    "4. Citação orgânica ou linha discreta final: cite a fonte de apoio (presente nos metadados da base) de forma leve e orgânica "
    "no próprio texto pedagógico (ex: 'Registro multimodal (baseado nas diretrizes do DUA 3.0 / CAST): peça que ela...') "
    "OU adicione apenas uma única linha discreta em itálico no final: '*Fonte base: [nome da fonte]*', citando estritamente os "
    "documentos utilizados para construir aquela resposta específica.\n"
    "5. Regra de Atribuição e Fim de Rodapé Fixo:\n"
    "   - NUNCA adicione blocos fixos de '### Referências e Fundamentação Científica' ao final de respostas para o professor.\n"
    "   - Se a mensagem for um bate-papo contínuo ou esclarecimento sem consulta a arquivos da base, NÃO adicione nenhum rodapé.\n"
    "6. Fluidez conversacional no chat: em diálogos em andamento, responda DIRETAMENTE à pergunta ou comentário do professor "
    "de forma ágil e natural, mantendo o assunto que estavam conversando. NÃO reinicie planos do zero nem gere tabelas longas "
    "quando o professor fizer perguntas simples de acompanhamento.\n"
    "7. Olhar sensível ao estudante: adapte sempre as sugestões com carinho e respeito às características, hiperfocos e "
    "necessidades do aluno mencionado.\n"
    "8. Redirecionamento gentil: se a pergunta fugir totalmente de contexto escolar/pedagógico, redirecione com simpatia para o apoio educacional.\n"
    "9. Limites Éticos, Clínicos e Postura Inclusiva:\n"
    "   - Diagnóstico Clínico (DSM-5/CID): NUNCA emita diagnóstico clínico ou médico nem valide se o aluno preenche critérios diagnósticos. Explique com acolhimento que o diagnóstico cabe exclusivamente a profissionais de saúde/equipe multiprofissional, e ofereça apoio nas observações e adaptações pedagógicas.\n"
    "   - Comunicação com a Família: Oriente o professor a NUNCA rotular ou afirmar um diagnóstico clínico para a família sem laudo; recomende compartilhar observações concretas do cotidiano escolar com empatia, escuta ativa e foco nas estratégias de apoio.\n"
    "   - Medicação e Posologia: JAMAIS indique medicamentos, ajustes de dose ou condutas farmacológicas. Esclareça que prescrições são ato exclusivamente médico e redirecione para estratégias pedagógicas em sala de aula.\n"
    "   - Postura Anticapacitista: Diante de desabafos ou falas desacreditando o potencial do estudante (ex: 'nunca vai aprender'), acolha o cansaço do professor sem julgamento, mas reafirme com firmeza ética e base científica que todo estudante pode aprender quando as barreiras são removidas com adaptações adequadas (DUA/PEI).\n"
    "10. Privacidade e Proteção de Dados (LGPD): NUNCA solicite, mencione, armazene ou replique números de CPF, RG, telefones "
    "ou documentos civis de estudantes, responsáveis ou professores. Mantenha o foco exclusivamente no perfil de aprendizagem e estratégias inclusivas."
)


def _montar_prompt_completo(prompt_usuario: str, contexto_rag: Optional[str] = None) -> str:
    """Combina o contexto documental do RAG com a pergunta do professor."""
    if not contexto_rag or not contexto_rag.strip():
        return prompt_usuario

    return (
        f"--- CONTEXTO PEDAGÓGICO E LEGAL RECUPERADO (RAG) ---\n"
        f"{contexto_rag.strip()}\n"
        f"--- FIM DO CONTEXTO ---\n\n"
        f"Pergunta / Solicitação do Professor:\n{prompt_usuario}"
    )


def _gerar_via_groq(prompt: str, api_key: str, model: str) -> str:
    """Gera resposta síncrona através da API da Groq."""
    if not Groq:
        raise RuntimeError("Biblioteca 'groq' não instalada.")
    client = Groq(api_key=api_key)
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA_METAMORPHIA},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_completion_tokens=2048,
    )
    return completion.choices[0].message.content or ""


def _gerar_via_groq_stream(prompt: str, api_key: str, model: str) -> Iterator[str]:
    """Gera resposta em fluxo (streaming) através da API da Groq."""
    if not Groq:
        raise RuntimeError("Biblioteca 'groq' não instalada.")
    client = Groq(api_key=api_key)
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA_METAMORPHIA},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_completion_tokens=2048,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


_JANELA_MINUTO_SEGUNDOS = 60.0
_JANELA_DIA_SEGUNDOS = 86_400.0
_REQUISICOES_MINUTO: deque[float] = deque()
_REQUISICOES_DIA: deque[float] = deque()
_LOCK_RATE_LIMIT = Lock()


def _aguardar_cota_gemini(
    max_rpm: Optional[int] = None, max_rpd: Optional[int] = None
) -> None:
    """Garante que as chamadas à API do Gemini nunca ultrapassem o limite de RPM (15) e RPD (500)."""
    settings = get_settings()
    limite_rpm = max(1, int(max_rpm or getattr(settings, "GEMINI_MAX_RPM", 15)))
    limite_rpd = max(1, int(max_rpd or getattr(settings, "GEMINI_MAX_RPD", 500)))

    while True:
        with _LOCK_RATE_LIMIT:
            agora = time.monotonic()
            while _REQUISICOES_MINUTO and (agora - _REQUISICOES_MINUTO[0]) >= _JANELA_MINUTO_SEGUNDOS:
                _REQUISICOES_MINUTO.popleft()
            while _REQUISICOES_DIA and (agora - _REQUISICOES_DIA[0]) >= _JANELA_DIA_SEGUNDOS:
                _REQUISICOES_DIA.popleft()

            if len(_REQUISICOES_DIA) >= limite_rpd:
                raise RuntimeError(
                    f"Limite diário de {limite_rpd} requisições (RPD) do Gemini atingido."
                )

            if len(_REQUISICOES_MINUTO) < limite_rpm:
                _REQUISICOES_MINUTO.append(agora)
                _REQUISICOES_DIA.append(agora)
                return

            espera = max(0.05, _REQUISICOES_MINUTO[0] + _JANELA_MINUTO_SEGUNDOS - agora)

        time.sleep(espera)


def _gerar_via_gemini(prompt: str, api_key: str, model: str) -> str:
    """Gera resposta síncrona através da API Google Gemini respeitando os limites de RPM (15) e RPD (500)."""
    if not genai:
        raise RuntimeError("Biblioteca 'google-genai' não instalada.")
    _aguardar_cota_gemini()
    client = genai.Client(api_key=api_key)
    prompt_completo = f"Instrução do Sistema:\n{PROMPT_SISTEMA_METAMORPHIA}\n\n{prompt}"
    response = client.models.generate_content(model=model, contents=prompt_completo)
    return response.text or ""


def _gerar_via_gemini_stream(prompt: str, api_key: str, model: str) -> Iterator[str]:
    """Gera resposta em fluxo (streaming) através da API Google Gemini respeitando os limites de RPM (15) e RPD (500)."""
    if not genai:
        raise RuntimeError("Biblioteca 'google-genai' não instalada.")
    _aguardar_cota_gemini()
    client = genai.Client(api_key=api_key)
    prompt_completo = f"Instrução do Sistema:\n{PROMPT_SISTEMA_METAMORPHIA}\n\n{prompt}"
    stream = client.models.generate_content_stream(model=model, contents=prompt_completo)
    for chunk in stream:
        if chunk.text:
            yield chunk.text


def _gerar_via_mock(prompt: str) -> str:
    """Gera resposta pedagógica mock padrão autoral para testes."""
    return (
        "Com base nas diretrizes do Desenho Universal para a Aprendizagem (DUA) e no "
        "Decreto nº 12.686/2025, sugerem-se as seguintes adaptações pedagógicas:\n\n"
        "1. **Representação Múltipla:** Apresentação visual de comandos passo a passo com suporte iconográfico.\n"
        "2. **Ação e Expressão:** Flexibilização de tempo de realização e permissão de resposta oral ou digital.\n"
        "3. **Engajamento e Rotina:** Previsibilidade de etapas para redução de sobrecarga sensorial e ansiedade."
    )


def gerar_resposta(
    prompt: str,
    contexto_rag: Optional[str] = None,
    fontes_referencia: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    aplicar_guardrails: bool = True,
    formato_referencias: str = "discreto",
) -> str:
    """Pipeline principal de geração de resposta da MetamorphIA.

    1. Aplica guardrails de entrada (Prompt Injection, Escopo, Dados Sensíveis).
    2. Concatena contexto do RAG.
    3. Executa a chamada no provedor de IA configurado (Groq / Gemini / Mock).
    4. Aplica guardrail de referências finais obrigatórias.
    """
    settings = get_settings()
    fontes = fontes_referencia or []

    # 1. Guardrails de Entrada
    if aplicar_guardrails:
        valido, prompt_seguro, msg_erro = validar_e_higienizar_entrada(prompt)
        if not valido:
            return msg_erro
    else:
        prompt_seguro, _ = sanitizar_dados_sensiveis(prompt)

    contexto_rag_seguro = None
    if contexto_rag:
        contexto_rag_seguro, _ = sanitizar_dados_sensiveis(contexto_rag)

    prompt_final = _montar_prompt_completo(prompt_seguro, contexto_rag_seguro)
    provider = settings.LLM_PROVIDER.lower()

    # 2. Seleção de Provedor (Gemini priorizado)
    resposta_bruta = ""
    if provider == "mock":
        resposta_bruta = _gerar_via_mock(prompt_final)
    elif provider == "gemini" or (provider == "auto" and settings.GEMINI_API_KEY):
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if api_key:
            try:
                resposta_bruta = _gerar_via_gemini(
                    prompt_final, api_key, model or settings.GEMINI_MODEL
                )
            except Exception:
                groq_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
                if groq_key:
                    try:
                        resposta_bruta = _gerar_via_groq(
                            prompt_final, groq_key, model or settings.GROQ_MODEL
                        )
                    except Exception:
                        resposta_bruta = _gerar_via_mock(prompt_final)
                else:
                    resposta_bruta = _gerar_via_mock(prompt_final)
        else:
            resposta_bruta = _gerar_via_mock(prompt_final)
    elif provider == "groq" or (provider == "auto" and settings.GROQ_API_KEY):
        api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        if api_key:
            try:
                resposta_bruta = _gerar_via_groq(
                    prompt_final, api_key, model or settings.GROQ_MODEL
                )
            except Exception:
                gemini_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
                if gemini_key:
                    try:
                        resposta_bruta = _gerar_via_gemini(
                            prompt_final, gemini_key, model or settings.GEMINI_MODEL
                        )
                    except Exception:
                        resposta_bruta = _gerar_via_mock(prompt_final)
                else:
                    resposta_bruta = _gerar_via_mock(prompt_final)
        else:
            resposta_bruta = _gerar_via_mock(prompt_final)
    else:
        resposta_bruta = _gerar_via_mock(prompt_final)

    # 3. Guardrail de Saída: Assegura referências finais apenas quando solicitado
    if aplicar_guardrails:
        return garantir_referencias_finais(resposta_bruta, fontes, formato=formato_referencias)
    return resposta_bruta


def gerar_resposta_stream(
    prompt: str,
    contexto_rag: Optional[str] = None,
    fontes_referencia: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    aplicar_guardrails: bool = True,
) -> Iterator[str]:
    """Gera streaming de tokens com verificação de guardrails."""
    settings = get_settings()

    if aplicar_guardrails:
        valido, prompt_seguro, msg_erro = validar_e_higienizar_entrada(prompt)
        if not valido:
            yield msg_erro
            return
    else:
        prompt_seguro, _ = sanitizar_dados_sensiveis(prompt)

    contexto_rag_seguro = None
    if contexto_rag:
        contexto_rag_seguro, _ = sanitizar_dados_sensiveis(contexto_rag)

    prompt_final = _montar_prompt_completo(prompt_seguro, contexto_rag_seguro)
    provider = settings.LLM_PROVIDER.lower()

    if provider == "mock":
        yield _gerar_via_mock(prompt_final)
        return

    if provider == "gemini" or (provider == "auto" and settings.GEMINI_API_KEY):
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if api_key:
            try:
                yield from _gerar_via_gemini_stream(prompt_final, api_key, model or settings.GEMINI_MODEL)
                return
            except Exception:
                pass

    if provider == "groq" or (provider == "auto" and settings.GROQ_API_KEY):
        api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        if api_key:
            try:
                yield from _gerar_via_groq_stream(prompt_final, api_key, model or settings.GROQ_MODEL)
                return
            except Exception:
                pass

    yield _gerar_via_mock(prompt_final)