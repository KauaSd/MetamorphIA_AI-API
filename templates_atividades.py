"""Catálogo de templates pedagógicos para criação de atividades adaptadas por perfil de aluno."""

from __future__ import annotations

import re
from typing import Any, Optional

TEMPLATES_PEDAGOGICOS: dict[str, dict[str, Any]] = {
    "tea_rotina_visual": {
        "id": "tea_rotina_visual",
        "nome": "Rotina Visual e Passo a Passo Previsível",
        "descricao": "Estrutura com sequência visual clara (Começo, Meio e Fim), instruções literais sem metáforas e apoio com hiperfoco do estudante.",
        "perfis_recomendados": ["tea", "autismo", "espectro autista"],
        "instrucao_prompt": (
            "ESTRUTURA DO TEMPLATE (Rotina Visual e Passo a Passo Previsível - TEA):\n"
            "1. QUADRO DE PREVISIBILIDADE (O que vamos fazer hoje / Quanto tempo dura / Como saber que terminou).\n"
            "2. CONEXÃO COM O INTERESSE/HIPERFOCO DO ESTUDANTE (introdução concreta e literal).\n"
            "3. ATIVIDADE EM ETAPAS CURTAS E NUMERADAS (1 comando por linha, linguagem literal, com apoio visual sugerido).\n"
            "4. QUESTÕES COM OPÇÕES VISUAIS / MÚLTIPLA ESCOLHA CLARA.\n"
            "5. FECHAMENTO E AUTORREGULAÇÃO (check final de conclusão)."
        ),
    },
    "tdah_blocos_foco": {
        "id": "tdah_blocos_foco",
        "nome": "Missões Curtas com Checklist de Foco",
        "descricao": "Divide a atividade em microdesafios rápidos com caixas de seleção (checklists), pausas ativas e comandos diretos.",
        "perfis_recomendados": ["tdah", "déficit de atenção", "hiperatividade", "tod"],
        "instrucao_prompt": (
            "ESTRUTURA DO TEMPLATE (Missões Curtas com Checklist de Foco - TDAH):\n"
            "1. META RÁPIDA DA MISSÃO (1 frase direta e motivadora).\n"
            "2. BLOCO 1 - AQUECIMENTO RÁPIDO (2 questões curtas com '[ ] Concluído').\n"
            "3. PAUSA DE MOVIMENTO / RESPIRAÇÃO (30 segundos entre blocos).\n"
            "4. BLOCO 2 - DESAFIO PRINCIPAL (enunciados enxutos, palavras-chave em destaque e espaço objetivo de resposta).\n"
            "5. CHECKLIST FINAL DO ESTUDANTE (3 itens simples para revisar antes de entregar)."
        ),
    },
    "dislexia_leitura_facilitada": {
        "id": "dislexia_leitura_facilitada",
        "nome": "Leitura Facilitada e Apoio Fonológico/Visual",
        "descricao": "Frases curtas na ordem direta, glossário visual de palavras-chave, espaçamento amplo e opção de resposta multimodal.",
        "perfis_recomendados": ["dislexia", "leitura", "disortografia", "linguagem"],
        "instrucao_prompt": (
            "ESTRUTURA DO TEMPLATE (Leitura Facilitada e Apoio Visual - Dislexia):\n"
            "1. PALAVRAS-CHAVE DO TEMA (máximo 4 termos explicados em frases curtas e diretas).\n"
            "2. TEXTO-BASE ADAPTADO (parágrafos de no máximo 2 linhas, frases na ordem sujeito-verbo-objeto, palavras essenciais em negrito).\n"
            "3. QUESTÕES DE COMPREENSÃO COM APOIO (associação, lacunas com banco de palavras ou múltipla escolha curta).\n"
            "4. OPÇÃO DE EXPRESSÃO MULTIMODAL (resposta oral, esquema ou desenho)."
        ),
    },
    "discalculia_passo_concreto": {
        "id": "discalculia_passo_concreto",
        "nome": "Raciocínio Concreto-Visual Passo a Passo",
        "descricao": "Representação concreta de quantidades, exemplo resolvido passo a passo e tabela organizadora para cálculos.",
        "perfis_recomendados": ["discalculia", "matemática", "di", "deficiência intelectual"],
        "instrucao_prompt": (
            "ESTRUTURA DO TEMPLATE (Raciocínio Concreto-Visual Passo a Passo - Discalculia/DI):\n"
            "1. MATERIAL CONCRETO DE APOIO (o que usar na mesa: blocos, tampinhas, desenhos).\n"
            "2. EXEMPLO RESOLVIDO PASSO A PASSO (mostrando exatamente como pensar e montar).\n"
            "3. EXERCÍCIOS GUIADOS (com quadro organizador: 'O que eu sei' / 'Desenho ou representação' / 'Resultado').\n"
            "4. DESAFIO PRÁTICO DO COTIDIANO."
        ),
    },
    "dua_multimodal_geral": {
        "id": "dua_multimodal_geral",
        "nome": "Template DUA Multimodal (Engajamento, Representação e Ação)",
        "descricao": "Atividade estruturada nos 3 princípios do DUA com múltiplas formas de apresentação e escolha de formato de resposta.",
        "perfis_recomendados": ["dua", "inclusão", "geral"],
        "instrucao_prompt": (
            "ESTRUTURA DO TEMPLATE (DUA Multimodal):\n"
            "1. CONEXÃO E ENGAJAMENTO (contextualização prática e acolhedora).\n"
            "2. CONTEÚDO EM MÚLTIPLOS FORMATOS (resumo visual + explicação passo a passo).\n"
            "3. QUESTÕES GRADUAIS (do nível concreto/guiado ao nível autônomo).\n"
            "4. ESCOLHA DE EXPRESSÃO (o estudante pode responder por escrito, esquema visual ou explicação oral)."
        ),
    },
}


def obter_template_por_id(template_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not template_id:
        return None
    return TEMPLATES_PEDAGOGICOS.get(template_id.strip().lower())


def recomendar_templates_para_perfil(
    neurodivergencia: str,
    desc_aluno: str = "",
    limite: int = 3,
) -> list[dict[str, Any]]:
    """Retorna os templates pedagógicos mais adequados ao perfil e neurodivergência do aluno."""
    texto_perfil = f"{neurodivergencia or ''} {desc_aluno or ''}".lower()
    pontuados: list[tuple[int, str, dict[str, Any]]] = []

    ordem_padrao = list(TEMPLATES_PEDAGOGICOS.keys())
    for idx, (tid, tpl) in enumerate(TEMPLATES_PEDAGOGICOS.items()):
        score = 0
        for termo in tpl["perfis_recomendados"]:
            if termo in texto_perfil:
                score += 10
        if tid == "dua_multimodal_geral":
            score += 1
        pontuados.append((score, -idx, tpl))

    pontuados.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selecionados = [
        {
            "opcao": i + 1,
            "id": tpl["id"],
            "nome": tpl["nome"],
            "descricao": tpl["descricao"],
        }
        for i, (_, _, tpl) in enumerate(pontuados[: max(1, limite)])
    ]
    return selecionados


def identificar_template_na_mensagem(
    mensagem: str,
    opcoes_ids: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Identifica se a mensagem do professor escolhe um template pelo número (1, 2, 3), ID ou nome."""
    txt = (mensagem or "").strip().lower()
    if not txt:
        return None

    # 1. Verifica ID explícito na mensagem
    for tid, tpl in TEMPLATES_PEDAGOGICOS.items():
        if tid in txt or tpl["nome"].lower() in txt:
            return tpl

    lista_ids = opcoes_ids or list(TEMPLATES_PEDAGOGICOS.keys())

    # 2. Verifica escolha por número ("1", "opção 2", "template 1", "primeira", "segunda", "terceira")
    mapa_ordinais = {
        "primeira": 1,
        "primeiro": 1,
        "segunda": 2,
        "segundo": 2,
        "terceira": 3,
        "terceiro": 3,
    }
    for palavra, num in mapa_ordinais.items():
        if re.search(rf"\b{palavra}\b", txt) and 1 <= num <= len(lista_ids):
            return TEMPLATES_PEDAGOGICOS.get(lista_ids[num - 1])

    m_num = re.search(r"^\s*(\d)\s*$|(?:\bop[cç][aã]o|\btemplate|\bitem|\bescolho|\bquero\s+a?\s*)\s*(\d)\b", txt)
    if m_num:
        digito = int(m_num.group(1) or m_num.group(2))
        if 1 <= digito <= len(lista_ids):
            return TEMPLATES_PEDAGOGICOS.get(lista_ids[digito - 1])

    # 3. Palavras-chave diretas
    if "rotina visual" in txt or "previs" in txt:
        return TEMPLATES_PEDAGOGICOS["tea_rotina_visual"]
    if "checklist" in txt or "miss" in txt or "blocos" in txt:
        return TEMPLATES_PEDAGOGICOS["tdah_blocos_foco"]
    if "leitura facilitada" in txt or "fonol" in txt:
        return TEMPLATES_PEDAGOGICOS["dislexia_leitura_facilitada"]
    if "concreto" in txt or "discalculia" in txt:
        return TEMPLATES_PEDAGOGICOS["discalculia_passo_concreto"]
    if "dua" in txt or "multimodal" in txt:
        return TEMPLATES_PEDAGOGICOS["dua_multimodal_geral"]

    return None
