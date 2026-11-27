from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

PALAVRAS_CHAVE_ESCOPO = {
    "neurodivergente", "neurodivergência", "neurodiversidade", "tea", "autismo", "autista",
    "tdah", "hiperatividade", "desatenção", "dislexia", "discalculia", "disgrafia", "dispraxia",
    "tod", "transtorno opositor", "deficiência", "intelectual", "down", "altas habilidades",
    "superdotação", "ah/sd", "tdl", "tourette", "tique", "tiques", "linguagem",
    "sensorial", "processamento auditivo", "cegueira", "baixa visão", "surdez",
    "pei", "plano educacional individualizado", "plano de ensino individualizado", "aee",
    "atendimento educacional especializado", "dua", "desenho universal", "aprendizagem",
    "adaptação", "adaptações", "adaptar", "adaptado", "adaptada",
    "atividade", "atividades", "tarefa", "tarefas", "avaliação", "avaliações", "currículo",
    "conteúdo", "conteúdos", "ensino",
    "pedagógico", "pedagógica", "professor", "professora", "aluno", "alunos", "aluna", "estudante", "estudantes",
    "escola", "sala de aula", "aula", "aulas", "intervenção", "comportamento", "rotina", "transição",
    "transições", "crise", "crises", "leitura", "alfabetização", "fonológica", "matemática",
    "subtração", "adição", "multiplicação", "divisão",
    "dezena", "metodologia", "metodologias", "estratégia", "estratégias", "recurso", "recursos", "recurso visual", "material concreto",
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

MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA = (
    "Como assistente pedagógico da **MetamorphIA**, não gero códigos de programação, scripts, "
    "algoritmos nem forneço respostas prontas ou gabaritos de exercícios e atividades. "
    "Minha função é exclusivamente sugerir **estratégias de adaptação pedagógica** e recursos "
    "inclusivos alinhados ao perfil e ao PEI do estudante."
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


_PADRAO_SAUDACAO_OU_CONVERSACIONAL = re.compile(
    r"^(?:"
    r"o+i+|ol[aá]+|e\s*a[ií]|eae|opa|ei+|hey|hello|hi|"
    r"bom\s+dia|boa\s+tarde|boa\s+noite|"
    r"tudo\s+(?:bem|bom|certo)\??|"
    r"obrigad[oa]|valeu|vlw|ok(?:ay)?|certo|beleza|entendi|perfeito|"
    r"sim|n[aã]o|pode\s+ser|vamos|ajuda|me\s+ajuda|socorro|como\s+funciona\??|"
    r"(?:gerar\s+|enviar\s+|baixar\s+)?documento|baixar\s+pei|"
    r"(?:op[cç][aã]o\s*)?[1-4]"
    r")[\s!.,?]*$",
    re.IGNORECASE,
)


def _remover_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )


_PADROES_ESCOPO = [
    re.compile(rf"(?<!\w){re.escape(_remover_acentos(termo))}(?!\w)", re.IGNORECASE)
    for termo in PALAVRAS_CHAVE_ESCOPO
]

_TERMOS_PEDAGOGIA_INCLUSIVA = (
    "adaptar", "adaptação", "adaptações", "adaptada", "adaptado",
    "inclusão", "inclusivo", "inclusiva",
    "neurodivergente", "neurodivergência", "neurodiversidade",
    "tea", "autismo", "autista", "tdah", "dislexia", "discalculia",
    "disgrafia", "dispraxia", "tod", "altas habilidades", "superdotação",
    "pei", "aee", "dua", "desenho universal",
)

_PADROES_PEDAGOGIA_INCLUSIVA = [
    re.compile(rf"(?<!\w){re.escape(_remover_acentos(termo))}(?!\w)", re.IGNORECASE)
    for termo in _TERMOS_PEDAGOGIA_INCLUSIVA
]

_PADROES_FORA_ESCOPO_EXPLICITO = [
    re.compile(r"\btrabalhos?\s+(?:escolar(?:es)?|da\s+escola)\b", re.IGNORECASE),
    re.compile(r"\b(?:dever|li[çc][aã]o|tarefa)\s+de\s+casa\b", re.IGNORECASE),
    re.compile(r"\b(?:meu|minha)\s+(?:trabalho|tarefa|prova|reda[çc][aã]o|li[çc][aã]o)\b", re.IGNORECASE),
    re.compile(r"\bo\s+que\s+(?:est[aá]|t[aá]|ta)\s+errado\b", re.IGNORECASE),
    re.compile(r"\b(?:corrij[ao]|revise)\s+(?:esse|este|o)\s+texto\b", re.IGNORECASE),
    re.compile(r"\bfa[çc]a\s+(?:esse|este|o|meu)\s+trabalho\b", re.IGNORECASE),
]

_PADROES_PROGRAMACAO_OU_SCRIPT = [
    re.compile(
        r"\b(?:codigo|c[oó]digo|script|programa|algoritmo|fun[cç][aã]o|classe|sistema|api)\s+"
        r"(?:em|de|com|usando|na\s+linguagem)\s+"
        r"(?:python|javascript|typescript|java|c\+\+|c#|php|ruby|golang|rust|sql|html|css|bash|powershell|node)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:python|javascript|typescript|c\+\+|c#|golang|rust|powershell|sql|html|css|react|node\.js)\b"
        r".{0,60}\b(?:codigo|c[oó]digo|script|gerar|escreva|crie|fazer|fa[cç]a|resolva|funcao|fun[cç][aã]o|loop|variavel|vari[aá]vel)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:gerar|escrev[ae]|cri[ae]|fa[cç]a|monte|gere|mand[ae])\s+"
        r"(?:um\s+|o\s+)?(?:codigo|c[oó]digo|script|algoritmo|programa(?:\s+de\s+computador)?|promptscript)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bprompt\s*script\b", re.IGNORECASE),
    re.compile(r"```(?:python|js|javascript|ts|typescript|java|sql|html|css|bash|sh|c|cpp|php)?\b", re.IGNORECASE),
]

_PADROES_RESPOSTA_PRONTA = [
    re.compile(
        r"\b(?:qual|me\s+d[eê]|diga|responda|resolva|calcule)\s+"
        r"(?:a\s+|o\s+)?(?:resposta\s+(?:certa|correta)|gabarito|resultado\s+(?:de|da|do)|essa\s+conta|essa\s+equa[cç][aã]o|essa\s+quest[aã]o)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bresolva\s+(?:essa|esta|a|o)\s+(?:conta|equa[cç][aã]o|exerc[ií]cio|problema|atividade|quest[aã]o)\b", re.IGNORECASE),
    re.compile(r"\b(?:escreva|fa[cç]a)\s+(?:uma|a|minha)\s+reda[cç][aã]o\b", re.IGNORECASE),
]

_PADROES_CODIGO_SAIDA = [
    re.compile(r"```(?:python|js|javascript|ts|typescript|java|sql|html|css|bash|sh|c|cpp|php)?\s*\n", re.IGNORECASE),
    re.compile(r"(?m)^\s*(?:def\s+\w+\s*\(|function\s+\w+\s*\(|class\s+\w+\s*[:{]|import\s+\w+|SELECT\s+.+\s+FROM\s+)", re.IGNORECASE),
]


def detectar_pedido_programacao_ou_resposta_pronta(texto: str) -> tuple[bool, str]:
    """Bloqueia pedidos de código/scripts ou respostas prontas mesmo quando citam o aluno."""
    if not texto:
        return False, ""
    texto_sem_acento = _remover_acentos(texto)
    for padrao in _PADROES_PROGRAMACAO_OU_SCRIPT:
        if padrao.search(texto) or padrao.search(texto_sem_acento):
            return True, MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA
    for padrao in _PADROES_RESPOSTA_PRONTA:
        if padrao.search(texto) or padrao.search(texto_sem_acento):
            return True, MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA
    return False, ""


def auditar_resposta_ia(resposta: str) -> str:
    """Garante na saída que a IA nunca devolva blocos de código de programação."""
    if not resposta:
        return resposta
    for padrao in _PADROES_CODIGO_SAIDA:
        if padrao.search(resposta):
            return MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA
    return resposta


def _contem_nome_contexto(texto_lower: str, nomes_contexto: Sequence[str] | None) -> bool:
    if not nomes_contexto:
        return False
    tokens_texto = set(re.findall(r"\b\w+\b", texto_lower))
    for nome in nomes_contexto:
        nome_norm = _remover_acentos((nome or "").strip().lower())
        if not nome_norm:
            continue
        if len(nome_norm) >= 3 and re.search(rf"(?<!\w){re.escape(nome_norm)}(?!\w)", texto_lower):
            return True
        for parte in re.findall(r"\b\w+\b", nome_norm):
            if len(parte) >= 3 and parte in tokens_texto:
                return True
    return False


def eh_pedido_explicitamente_fora_escopo(texto: str) -> bool:
    if not texto:
        return False
    texto_sem_acento = _remover_acentos(texto)
    if any(p.search(texto_sem_acento) for p in _PADROES_PEDAGOGIA_INCLUSIVA):
        return False
    return any(p.search(texto) for p in _PADROES_FORA_ESCOPO_EXPLICITO)


def validar_escopo_pedagogico(
    texto: str,
    nomes_contexto: Sequence[str] | None = None,
    similaridade_vetorial: float | None = None,
) -> tuple[bool, str]:
    if not texto or not texto.strip():
        return False, "Por favor, digite sua pergunta pedagógica ou solicitação de adaptação."

    texto_limpo = texto.strip()
    texto_lower = _remover_acentos(texto_limpo.lower())

    eh_proibido, msg_proibicao = detectar_pedido_programacao_ou_resposta_pronta(texto_limpo)
    if eh_proibido:
        return False, msg_proibicao

    if eh_pedido_explicitamente_fora_escopo(texto_limpo):
        return False, MENSAGEM_RECUSA_ESCOPO

    if any(p.search(texto_lower) for p in _PADROES_ESCOPO):
        return True, ""

    if _contem_nome_contexto(texto_lower, nomes_contexto):
        return True, ""

    if similaridade_vetorial is not None and float(similaridade_vetorial) >= 0.35:
        return True, ""

    if _PADRAO_SAUDACAO_OU_CONVERSACIONAL.match(texto_limpo):
        return True, ""

    # Aceita saudações combinadas (ex: "oii, tudo bem?", "bom dia! pode me ajudar?")
    if re.match(r"^(?:o+i+|ol[aá]+|bom\s+dia|boa\s+tarde|boa\s+noite|e\s*a[ií]|eae)\b", texto_lower):
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
    mensagem_usuario: str,
    bloquear_se_houver_pii: bool = True,
    nomes_contexto: Sequence[str] | None = None,
    similaridade_vetorial: float | None = None,
) -> tuple[bool, str, str]:
    eh_ataque, msg_injection = detectar_prompt_injection(mensagem_usuario)
    if eh_ataque:
        return False, "", msg_injection

    no_escopo, msg_escopo = validar_escopo_pedagogico(
        mensagem_usuario,
        nomes_contexto=nomes_contexto,
        similaridade_vetorial=similaridade_vetorial,
    )
    if not no_escopo:
        return False, "", msg_escopo

    texto_seguro, dados_encontrados = sanitizar_dados_sensiveis(mensagem_usuario)
    if dados_encontrados and bloquear_se_houver_pii:
        return False, "", MENSAGEM_ALERTA_DADOS_SENSIVEIS

    return True, texto_seguro, ""
