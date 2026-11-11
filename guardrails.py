from __future__ import annotations

import re
from typing import Any

PALAVRAS_CHAVE_ESCOPO = {
    "neurodivergente", "neurodivergência", "neurodiversidade", "tea", "autismo", "autista",
    "tdah", "hiperatividade", "desatenção", "dislexia", "discalculia", "disgrafia", "dispraxia",
    "tod", "transtorno opositor", "deficiência", "intelectual", "down", "altas habilidades",
    "superdotação", "ah/sd", "tdl", "tourette", "tique", "tiques", "linguagem",
    "sensorial", "processamento auditivo", "cegueira", "baixa visão", "surdez",
    "pei", "plano educacional individualizado", "plano de ensino individualizado", "aee",
    "atendimento educacional especializado", "dua", "desenho universal", "aprendizagem",
    "adaptação", "adaptar", "atividade", "tarefa", "tarefas", "avaliação", "currículo",
    "pedagógico", "pedagógica", "professor", "professora", "aluno", "aluna", "estudante",
    "escola", "sala de aula", "aula", "intervenção", "comportamento", "rotina", "transição",
    "transições", "crise", "crises", "leitura", "alfabetização", "fonológica", "matemática",
    "dezena", "metodologia", "estratégia", "recurso", "recurso visual", "material concreto",
    "tempo estendido", "enriquecimento", "entediado", "atrapalhando", "como lidar",
    "mediação", "inclusão", "inclusivo", "inclusiva", "decreto", "legislação", "orientação",
}

PADROES_PROMPT_INJECTION = [
    r"(?i)\b(dan|jailbreak)\b",
    r"(?i)\b(developer\s+mode|modo\s+desenvolvedor)\b",
    r"(?i)\bignore\s+(todas\s+as\s+|as\s+|all\s+)?(instruções|regras|instructions)\b",
    r"(?i)\besqueça\s+(as\s+|todas\s+as\s+)?(regras|instruções|diretrizes)\b",
    r"(?i)\bforget\s+(all\s+)?(instructions|rules|previous\s+prompts)\b",
    r"(?i)\b(system\s+override|sobrescrever\s+sistema|desativar\s+filtros)\b",
    r"(?i)\b(qual\s+é\s+o\s+seu\s+prompt|revele\s+suas\s+instruções|mostre\s+seu\s+system\s+prompt)\b",
    r"(?i)\b(show\s+(me\s+)?(your\s+)?system\s+prompt|repeat\s+the\s+instructions\s+above)\b",
    r"(?i)```\s*(system|instruction|override)",
    r"(?i)\[\s*system\s*:\s*override\s*\]",
]

MENSAGEM_RECUSA_ESCOPO = (
    "Como assistente da **MetamorphIA**, minha atuação é estritamente dedicada ao "
    "**apoio pedagógico inclusivo**, auxiliando professores a **adaptar atividades** "
    "para alunos neurodivergentes (como TEA, TDAH, Dislexia, etc.) e na **elaboração de PEIs** "
    "(Planos Educacionais Individualizados). "
    "\n\nPor favor, envie uma dúvida ou solicitação relacionada à adaptação de conteúdos, "
    "estratégias de ensino inclusivo ou acompanhamento de estudantes."
)

MENSAGEM_ALERTA_INJECTION = (
    "Aviso de Segurança MetamorphIA: Sua mensagem contém comandos ou instruções "
    "que violam as diretrizes de segurança e integridade do assistente pedagógico. "
    "A requisição foi interrompida e não será processada fora dos limites éticos e pedagógicos."
)

MENSAGEM_ALERTA_DADOS_SENSIVEIS = (
    "Aviso de Privacidade MetamorphIA: Detectamos dados pessoais identificáveis ou sensíveis "
    "(como CPF, RG, telefone ou documento) na sua mensagem. Por diretrizes de proteção e "
    "conformidade com a LGPD, o sistema não aceita nem processa documentos civis de estudantes ou responsáveis. "
    "A requisição foi interrompida para proteger a privacidade. Por favor, reformule sua mensagem informando "
    "apenas o contexto e os objetivos pedagógicos."
)


def validar_cpf_digitos(cpf_apenas_digitos: str) -> bool:
    if len(cpf_apenas_digitos) != 11 or cpf_apenas_digitos == cpf_apenas_digitos[0] * 11:
        return False

    soma = sum(int(cpf_apenas_digitos[i]) * (10 - i) for i in range(9))
    resto = (soma * 10) % 11
    digito_1 = 0 if resto == 10 else resto
    if digito_1 != int(cpf_apenas_digitos[9]):
        return False

    soma = sum(int(cpf_apenas_digitos[i]) * (11 - i) for i in range(10))
    resto = (soma * 10) % 11
    digito_2 = 0 if resto == 10 else resto
    return digito_2 == int(cpf_apenas_digitos[10])


def sanitizar_dados_sensiveis(texto: str) -> tuple[str, list[str]]:
    if not texto:
        return "", []

    texto_sanitizado = str(texto)
    dados_encontrados: list[str] = []

    padrao_cpf_formatado = r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"
    if re.search(padrao_cpf_formatado, texto_sanitizado):
        texto_sanitizado = re.sub(padrao_cpf_formatado, "[CPF_PROTEGIDO]", texto_sanitizado)
        dados_encontrados.append("CPF_FORMATADO")

    padrao_cpf_rotulado = r"(?i)\b(?:cpf|c\.p\.f\.?)\s*[:=]?\s*(\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-/\s]?\d{2}|\d{11})\b"
    for match in re.finditer(padrao_cpf_rotulado, texto_sanitizado):
        trecho_cpf = match.group(1)
        if trecho_cpf != "[CPF_PROTEGIDO]":
            texto_sanitizado = texto_sanitizado.replace(trecho_cpf, "[CPF_PROTEGIDO]")
            dados_encontrados.append("CPF_ROTULADO")

    padrao_cpf_digitos = r"\b\d{11}\b"
    for match in re.finditer(padrao_cpf_digitos, texto_sanitizado):
        cpf = match.group()
        if validar_cpf_digitos(cpf):
            texto_sanitizado = texto_sanitizado.replace(cpf, "[CPF_PROTEGIDO]")
            dados_encontrados.append("CPF_NUMERICO")

    padrao_rg_formatado = r"\b\d{1,2}\.\d{3}\.\d{3}-[\dXx]\b"
    if re.search(padrao_rg_formatado, texto_sanitizado):
        texto_sanitizado = re.sub(padrao_rg_formatado, "[DOCUMENTO_PROTEGIDO]", texto_sanitizado)
        dados_encontrados.append("RG_FORMATADO")

    padrao_rg_rotulado = r"(?i)\b(?:rg|r\.g\.?|registro\s+geral|doc(?:umento)?)\b\s*[:=]?\s*((?=[0-9A-Za-z.\-]*\d{3})[0-9A-Za-z.\-]{6,14})\b"
    for match in re.finditer(padrao_rg_rotulado, texto_sanitizado):
        trecho_rg = match.group(1)
        if trecho_rg not in ("[DOCUMENTO_PROTEGIDO]", "[CPF_PROTEGIDO]"):
            texto_sanitizado = texto_sanitizado.replace(trecho_rg, "[DOCUMENTO_PROTEGIDO]")
            dados_encontrados.append("RG_ROTULADO")

    padrao_telefone = r"(?:\+55\s?)?(?:\(?\d{2}\)?\s?)(?:9\s?\d{4}[-\s]?\d{4}|\d{4}[-\s]?\d{4})\b"
    if re.search(padrao_telefone, texto_sanitizado):
        texto_sanitizado = re.sub(padrao_telefone, "[TELEFONE_PROTEGIDO]", texto_sanitizado)
        dados_encontrados.append("TELEFONE")

    padrao_email = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    if re.search(padrao_email, texto_sanitizado):
        texto_sanitizado = re.sub(padrao_email, "[EMAIL_PROTEGIDO]", texto_sanitizado)
        dados_encontrados.append("EMAIL")

    padrao_cartao = r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    for match in re.finditer(padrao_cartao, texto_sanitizado):
        cartao = match.group()
        if cartao.replace("-", "").replace(" ", "").isdigit():
            texto_sanitizado = texto_sanitizado.replace(cartao, "[DADO_SENSIVEL_REMOVIDO]")
            dados_encontrados.append("CARTAO")

    padrao_cep = r"(?i)\b(?:cep\s*[:=]?\s*)?(\d{5}-\d{3})\b"
    for match in re.finditer(padrao_cep, texto_sanitizado):
        cep = match.group(1)
        texto_sanitizado = texto_sanitizado.replace(cep, "[CEP_PROTEGIDO]")
        dados_encontrados.append("CEP")

    return texto_sanitizado, list(set(dados_encontrados))


def detectar_prompt_injection(texto: str) -> tuple[bool, str]:
    if not texto:
        return False, ""

    for padrao in PADROES_PROMPT_INJECTION:
        if re.search(padrao, texto):
            return True, MENSAGEM_ALERTA_INJECTION

    return False, ""


def validar_escopo_pedagogico(texto: str) -> tuple[bool, str]:
    if not texto or not texto.strip():
        return False, "Por favor, digite sua pergunta pedagógica ou solicitação de adaptação."

    texto_lower = texto.lower()

    if any(termo in texto_lower for termo in PALAVRAS_CHAVE_ESCOPO):
        return True, ""

    saudacoes_comuns = {"olá", "ola", "bom dia", "boa tarde", "boa noite", "ajuda", "socorro", "como funciona"}
    if any(s == texto_lower.strip() for s in saudacoes_comuns):
        return True, ""

    return False, MENSAGEM_RECUSA_ESCOPO


def garantir_referencias_finais(
    resposta_ia: str,
    fontes_consultadas: list[dict[str, Any]],
    formato: str = "discreto",
) -> str:
    if not resposta_ia or not fontes_consultadas:
        return resposta_ia

    nomes_fontes: list[str] = []
    nomes_vistos: set[str] = set()
    for f in fontes_consultadas:
        nome = f.get("titulo") or f.get("citacao_padrao") or f.get("source")
        if nome and nome not in nomes_vistos:
            nomes_vistos.add(nome)
            nomes_fontes.append(nome)

    if not nomes_fontes:
        return resposta_ia

    if len(nomes_fontes) == 1:
        texto_fontes = nomes_fontes[0]
    else:
        texto_fontes = ", ".join(nomes_fontes[:-1]) + " e " + nomes_fontes[-1]

    if "*Fonte base:" in resposta_ia or "*📚 Baseado" in resposta_ia:
        return resposta_ia

    return f"{resposta_ia.strip()}\n\n*Fonte base: {texto_fontes}*"


def validar_e_higienizar_entrada(
    mensagem_usuario: str, bloquear_se_houver_pii: bool = True
) -> tuple[bool, str, str]:
    eh_ataque, msg_injection = detectar_prompt_injection(mensagem_usuario)
    if eh_ataque:
        return False, "", msg_injection

    no_escopo, msg_escopo = validar_escopo_pedagogico(mensagem_usuario)
    if not no_escopo:
        return False, "", msg_escopo

    texto_seguro, dados_encontrados = sanitizar_dados_sensiveis(mensagem_usuario)
    if dados_encontrados and bloquear_se_houver_pii:
        return False, "", MENSAGEM_ALERTA_DADOS_SENSIVEIS

    return True, texto_seguro, ""
