"""1ª IA Analisadora (Modo Teste) para avaliar pertinência percentual antes da IA principal."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Optional

from config import get_settings
from guardrails import detectar_pedido_programacao_ou_resposta_pronta, sanitizar_dados_sensiveis
from llm_client import gerar_resposta_async

PROMPT_SISTEMA_ANALISADOR = (
    "Você é um auditor pedagógico rigoroso da plataforma MetamorphIA.\n"
    "Sua função é analisar a mensagem do professor comparando-a com o perfil do aluno, "
    "os PEIs já feitos (por disciplina) e as vivências recuperadas do banco vetorial.\n"
    "Responda EXCLUSIVAMENTE com um JSON válido, sem markdown e sem texto fora do JSON:\n"
    "{\n"
    '  "porcentagem_pertinencia": 0 a 100,\n'
    '  "relacionado_ao_aluno_ou_pei": true ou false,\n'
    '  "pede_resposta_pronta_ou_codigo": true ou false,\n'
    '  "disciplina_identificada": "nome da disciplina ou null",\n'
    '  "motivo": "explicação curta em 1 frase"\n'
    "}\n\n"
    "Critérios obrigatórios:\n"
    "- Se pedir código de programação, script, algoritmo ou promptscript (mesmo citando o aluno), "
    "marque pede_resposta_pronta_ou_codigo=true e porcentagem_pertinencia <= 10.\n"
    "- Se pedir resposta pronta/gabarito de exercício/prova/redação em vez de estratégia de adaptação, "
    "marque pede_resposta_pronta_ou_codigo=true e porcentagem_pertinencia <= 20.\n"
    "- Se for dúvida ou relato pedagógico real sobre o estudante (inclusive nova vivência em sala de aula), "
    "marque relacionado_ao_aluno_ou_pei=true e atribua nota >= 75."
)


@dataclass(frozen=True)
class ResultadoAnaliseEntrada:
    porcentagem: float
    limiar: float
    aprovado: bool
    motivo: str
    relacionado_ao_aluno_ou_pei: bool
    pede_resposta_pronta_ou_codigo: bool
    disciplina_identificada: Optional[str]
    latencia_ms: float


async def analisar_entrada_com_ia(
    mensagem: str,
    nome_aluno: str,
    neurodivergencia: str,
    trechos_vetoriais: list[dict[str, Any]],
    disciplinas_pei: list[str],
    limiar: Optional[float] = None,
) -> ResultadoAnaliseEntrada:
    """Executa a 1ª IA Analisadora e decide pela porcentagem se a 2ª IA pode responder."""
    settings = get_settings()
    limiar_efetivo = float(limiar if limiar is not None else settings.ANALYZER_MIN_SCORE)
    inicio = time.perf_counter()

    eh_proibido, msg_proib = detectar_pedido_programacao_ou_resposta_pronta(mensagem)
    if eh_proibido:
        return ResultadoAnaliseEntrada(
            porcentagem=0.0,
            limiar=limiar_efetivo,
            aprovado=False,
            motivo=msg_proib,
            relacionado_ao_aluno_ou_pei=False,
            pede_resposta_pronta_ou_codigo=True,
            disciplina_identificada=None,
            latencia_ms=round((time.perf_counter() - inicio) * 1000, 2),
        )

    msg_limpa, _ = sanitizar_dados_sensiveis(mensagem)
    resumo_vetores = "\n".join(
        f"- [{t.get('origem', 'vetor')} | {t.get('disciplina') or 'Geral'} | sim={t.get('similaridade', 0)}]: {str(t.get('texto', ''))[:260]}"
        for t in trechos_vetoriais[:5]
    ) or "Nenhum trecho vetorial prévio recuperado."

    prompt = (
        f"Estudante: {nome_aluno} ({neurodivergencia})\n"
        f"PEIs concluídos disponíveis: {', '.join(disciplinas_pei) or 'Geral'}\n"
        f"Trechos do banco vetorial (pgvector) do aluno:\n{resumo_vetores}\n\n"
        f"Mensagem do professor:\n{msg_limpa}"
    )

    try:
        if (settings.LLM_PROVIDER or "").strip().lower() == "mock":
            # Simulação determinística para testes quando LLM_PROVIDER=mock
            melhor_sim = max((float(t.get("similaridade", 0.0)) for t in trechos_vetoriais), default=0.0)
            pct = 85.0 if melhor_sim >= 0.25 or nome_aluno.lower().split()[0] in msg_limpa.lower() else 45.0
            dados = {
                "porcentagem_pertinencia": pct,
                "relacionado_ao_aluno_ou_pei": pct >= 50.0,
                "pede_resposta_pronta_ou_codigo": False,
                "disciplina_identificada": disciplinas_pei[0] if disciplinas_pei else None,
                "motivo": "Avaliação automática em modo mock baseada na aderência ao perfil/pgvector.",
            }
        else:
            bruto = await gerar_resposta_async(
                prompt=prompt,
                aplicar_guardrails=False,
                system_prompt=PROMPT_SISTEMA_ANALISADOR,
            )
            match = re.search(r"\{.*\}", bruto or "", re.DOTALL)
            dados = json.loads(match.group(0) if match else (bruto or "{}"))

        porcentagem = max(0.0, min(100.0, float(dados.get("porcentagem_pertinencia", 0.0))))
        relacionado = bool(dados.get("relacionado_ao_aluno_ou_pei", False))
        pede_proibido = bool(dados.get("pede_resposta_pronta_ou_codigo", False))
        disciplina_id = dados.get("disciplina_identificada")
        if disciplina_id in ("", "null", "None"):
            disciplina_id = None
        motivo = str(dados.get("motivo") or "Análise concluída.").strip()
        aprovado = (porcentagem >= limiar_efetivo) and relacionado and (not pede_proibido)
    except Exception:
        porcentagem = 75.0
        relacionado = True
        pede_proibido = False
        disciplina_id = None
        motivo = "Fallback seguro do analisador após erro de parsing."
        aprovado = porcentagem >= limiar_efetivo

    return ResultadoAnaliseEntrada(
        porcentagem=round(porcentagem, 2),
        limiar=limiar_efetivo,
        aprovado=aprovado,
        motivo=motivo,
        relacionado_ao_aluno_ou_pei=relacionado,
        pede_resposta_pronta_ou_codigo=pede_proibido,
        disciplina_identificada=disciplina_id,
        latencia_ms=round((time.perf_counter() - inicio) * 1000, 2),
    )
