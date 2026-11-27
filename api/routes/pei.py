from __future__ import annotations

import asyncio
import base64
import copy
import glob
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import uuid
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from auth_jwt import validar_permissao_chat_usuario, verificar_token_jwt
from config import get_settings
from db_client import (
    atualizar_descricao_aluno,
    atualizar_status_pei_disciplina_db,
    baixar_pei_docx_db,
    levantar_http_de_erro_db,
    listar_peis_aluno_db,
    obter_aluno,
    obter_pei_detalhe_db,
    obter_turma,
    salvar_pei_arquivo_db,
)
from guardrails import detectar_prompt_injection, sanitizar_dados_sensiveis
from llm_client import gerar_resposta
from memoria.queries import buscar_documentos_referencia
from memoria.student_vectors import dividir_texto_pei_em_chunks, indexar_contexto_aluno_pgvector
from schemas import (
    IndicadorDesenvolvimentoItem,
    MetaBimestreItem,
    PeiCriarRequest,
    PeiCriarResponse,
    PeiDetalhe,
    PeiImportarRequest,
    PeiInicialRequest,
    PeiInicialResponse,
    PeiPerguntaGeralRoteiro,
    PeiRespostaItem,
    PeiResumo,
    PeiSubperguntaItem,
)

router = APIRouter(tags=["pei"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PROMPT_SISTEMA_PEI = (
    "Você é um redator pedagógico especializado na elaboração do Plano Educacional Individualizado (PEI - "
    "Anexo IV da Resolução SEDUC nº 129/2025) seguindo rigorosamente o Roteiro MetamorphIA.\n\n"
    "REGRAS OBRIGATÓRIAS DE REDAÇÃO E FORMATAÇÃO DO PEI:\n"
    "1. NUNCA inclua citações de fontes, referências bibliográficas, '*Fonte base:...*', 'Fonte:', 'Referências:' "
    "ou menções a arquivos/documentos de apoio no texto do PEI.\n"
    "2. NUNCA use tabelas (proibido usar tabelas Markdown com '|', separadores '|---|' ou quadros tabulares).\n"
    "3. NUNCA use marcações Markdown cruas como '**negrito**', '*itálico*', '### títulos' ou blocos de código.\n"
    "4. NUNCA inclua saudações ('Olá, professor!'), conversas paralelas ou explicações fora do texto oficial do PEI.\n"
    "5. Escreva em português claro, objetivo e profissional, usando apenas parágrafos corridos e, quando útil, "
    "tópicos simples iniciados por '• '.\n"
    "6. NUNCA invente títulos fictícios de vídeos do YouTube, canais, livros, autores, jogos comerciais ou links/URLs; "
    "indique apenas formatos/tipos de recursos pedagógicos, critérios de acessibilidade e termos de busca.\n"
    "7. Privacidade LGPD: jamais inclua CPF, RG, telefone ou documentos civis.\n"
    "8. Coesão e foco nas respostas do professor: use as respostas enviadas pelo professor como eixo central do PEI, "
    "integrando-as de forma coesa. Você pode complementar com estratégias ou orientações pedagógicas que NÃO foram "
    "mencionadas explicitamente, desde que sejam diretamente alinhadas e centralizadas no que o professor pediu e "
    "escolheu — sem fugir do direcionamento dele nem despejar opções genéricas desconexas."
)


def _limpar_texto_resposta_pei(texto: str) -> str:
    """Remove fontes, tabelas Markdown e marcações cruas que não pertencem ao documento PEI."""
    if not texto:
        return ""
    linhas_limpas: list[str] = []
    for linha in str(texto).splitlines():
        ln = linha.strip()
        if not ln:
            if linhas_limpas and linhas_limpas[-1] != "":
                linhas_limpas.append("")
            continue
        # Remove linhas de fonte/referência
        if re.match(r"^\*{0,2}\s*(?:📚\s*)?(?:fonte(?:\s+base)?|refer[eê]ncias?|baseado\s+em)\s*[:\-]", ln, flags=re.I):
            continue
        # Remove linha separadora de tabela Markdown (ex: | --- | --- |)
        if re.match(r"^\|?[\s:\-]+\|[\s:\-\|]*$", ln):
            continue
        # Converte eventual linha de tabela Markdown (| a | b |) em tópico simples
        if ln.startswith("|") and ln.endswith("|") and ln.count("|") >= 2:
            celulas = [c.strip() for c in ln.strip("|").split("|") if c.strip()]
            if not celulas:
                continue
            # Ignora cabeçalhos genéricos de tabela
            if all(
                c.lower() in ("habilidade", "habilidades", "conteúdo", "conteúdos", "adaptação", "adaptações", "estratégia", "estratégias", "recurso", "recursos", "categoria", "descrição", "objetivo", "instrumento", "avaliação")
                for c in celulas
            ):
                continue
            ln = "• " + " — ".join(celulas)
        # Remove marcações Markdown cruas
        ln = re.sub(r"^#{1,6}\s+", "", ln)
        ln = re.sub(r"\*\*(.*?)\*\*", r"\1", ln)
        ln = re.sub(r"__(.*?)__", r"\1", ln)
        ln = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)", r"\1", ln)
        ln = re.sub(r"^\s*[-*]\s+", "• ", ln)
        linhas_limpas.append(ln)
    return "\n".join(linhas_limpas).strip()

PERGUNTA_INICIAL_PRIMEIRO_PEI = (
    "Percebi que é o seu primeiro PEI, por isso ainda não conheço esse aluno, "
    "poderia descrever o estudante, suas necessidades e como é em sala de aula "
    "em pelo menos 5 linhas? Já me ajuda muito!"
)

PERGUNTAS_GERAIS_MODELO: dict[int, str] = {
    1: "Quais conteúdos e habilidades do Currículo da Rede Estadual Paulista serão desenvolvidos no bimestre?",
    2: "Quais estratégias, intervenções pedagógicas e recursos de acessibilidade serão utilizados para favorecer o acesso, a participação e a aprendizagem do estudante?",
    3: "Quais instrumentos serão utilizados para acompanhar o aprendizado do estudante de forma inclusiva e individualizada?",
    4: "Quais vídeos, livros, jogos, exercícios ou outras atividades podem ser indicados para apoiar, complementar, suplementar e fortalecer o aprendizado do estudante neste componente curricular, considerando suas potencialidades, especificidades e ritmo de aprendizagem?",
}

NORTEAMENTO_VERMELHO_PERGUNTAS_GERAIS: dict[int, str] = {
    1: (
        "MOSTRAR O CONTEÚDO PREVISTO DO CURRÍCULO PAULISTA PARA AQUELE ANO/BIMESTRE. "
        "Considerar: as habilidades já consolidadas; o que ainda está em desenvolvimento; "
        "o que o estudante só realiza com ajuda; simplificação de ações, aumento do tempo de trabalho, "
        "divisão da habilidade em pequenas etapas, uso de recursos acessíveis sem retirar o conteúdo curricular; "
        "recursos de acessibilidade, uso de tecnologias assistivas, apoio de profissionais (quando houver), "
        "adaptações de materiais e linguagem. "
        "Registrar tudo no planejamento bimestral: os conteúdos e habilidades escolhidos; "
        "as adaptações necessárias; os recursos que serão usados; os apoios previstos."
    ),
    2: (
        "As ações devem considerar os princípios do Desenho Universal para a Aprendizagem - DUA e outras "
        "abordagens pedagógicas inclusivas que ampliem as possibilidades de engajamento, representação e expressão. "
        "Integrar de forma coesa as estratégias pedagógicas (explicar em partes menores, exemplos concretos, "
        "mais tempo, mediação em grupo, retomada de conteúdos, previsibilidade/rotina visual, instruções claras com apoio visual), "
        "as intervenções pedagógicas (apoio individual em atividades complexas, adaptação de quantidade e complexidade, "
        "ajuste da forma de participação oral/pictograma/escrita/digital) e os recursos de acessibilidade "
        "(material ampliado/fontes acessíveis, figuras/esquemas/pictogramas/CAA, tablet/computador, "
        "fones/abafadores/mobiliário adaptado, tecnologias assistivas)."
    ),
    3: (
        "Para isso, é essencial utilizar instrumentos diversificados e acessíveis, como observação direta, "
        "registros de desempenho, portfólios, autoavaliação mediada, produções orais, visuais ou práticas, "
        "e provas adaptadas com apoio de recursos de acessibilidade e tecnologia assistiva "
        "(observação diária, portfólio, avaliações práticas, checklist de habilidades, registro fotográfico, "
        "autoavaliação com apoio visual, conversas orientadas, produções em vídeo/áudio/desenho, rubricas). "
        "Princípio básico: A avaliação deve mostrar o progresso, mesmo que pequeno, e não comparar o estudante "
        "com o restante da turma, mas sim com ele mesmo."
    ),
    4: (
        "Indicar recursos pedagógicos que possam apoiar, de forma complementar ou suplementar, o aprendizado do estudante, "
        "com o objetivo de fortalecer sua compreensão dos conteúdos, contemplando: "
        "Vídeos (animações curtas explicando conteúdo, vídeos com linguagem simples, narração clara e exemplos visuais, "
        "vídeos dublados em Libras quando necessário, vídeos com passo a passo); "
        "Livros (histórias curtas com imagens, curiosidades sobre o tema, material paradidático com linguagem acessível, "
        "formatos ampliados ou digitais); "
        "Jogos (memória relacionados ao conteúdo, associação imagem-palavra/causa-efeito, jogos digitais educativos, "
        "quebra-cabeça com o conteúdo estudado, jogos de turnos que favorecem interação social); "
        "Atividades e exercícios (sequências didáticas com etapas curtas, atividades manipulativas de contar/ordenar/classificar, "
        "materiais concretos, exercícios de completar/ligar/ordenar/montar, atividades multissensoriais visuais/táteis/auditivas)."
    ),
}


def _localizar_modelo_docx() -> Path:
    raiz = Path(__file__).resolve().parents[2]
    padrao = str(raiz / "PEI-estilo" / "*MODELO*.docx")
    arquivos = glob.glob(padrao)
    if not arquivos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Modelo base de PEI em formato .docx não encontrado no servidor.",
        )
    return Path(arquivos[0])


def _obter_contexto_consolidado_aluno(
    desc_aluno: Optional[str],
    perfil_ia: Optional[str],
    descricao_enviada: Optional[str] = None,
) -> str:
    partes = []
    for texto in (descricao_enviada, perfil_ia, desc_aluno):
        if texto and texto.strip() and texto.strip().lower() not in ("string", "none", "null"):
            limpo, _ = sanitizar_dados_sensiveis(texto.strip())
            if limpo and limpo not in partes:
                partes.append(limpo)
    return "\n".join(partes).strip()


_NOMES_COMPONENTES_CURRICULARES: dict[str, str] = {
    "matematica": "Matemática",
    "portugues": "Língua Portuguesa",
    "l_portuguesa": "Língua Portuguesa",
    "lingua_portuguesa": "Língua Portuguesa",
    "ciencias": "Ciências",
    "artes": "Arte",
    "arte": "Arte",
    "historia": "História",
    "geografia": "Geografia",
    "ingles": "Língua Inglesa",
    "l_inglesa": "Língua Inglesa",
    "lingua_inglesa": "Língua Inglesa",
    "ed_fisica": "Educação Física",
    "ed._fisica": "Educação Física",
    "educacao_fisica": "Educação Física",
    "fisica": "Física",
    "quimica": "Química",
    "biologia": "Biologia",
    "filosofia": "Filosofia",
    "sociologia": "Sociologia",
    "ensino_religioso": "Ensino Religioso",
}


def _normalizar_nome_componente(componente: Optional[str]) -> str:
    bruto = (componente or "").strip()
    if not bruto:
        return "Língua Portuguesa"
    chave = (
        unicodedata.normalize("NFKD", bruto.lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    chave = re.sub(r"[\s.]+", "_", chave).strip("_")
    return _NOMES_COMPONENTES_CURRICULARES.get(chave, bruto)


def _opcoes_padrao_contextualizadas(
    nome_aluno: str,
    neurodivergencia: str,
    contexto_aluno: str,
    componente_curricular: str,
    ano_escolar: str,
    periodo_bimestre: int,
) -> tuple[str, dict[str, list[str]]]:
    conteudo_previsto = (
        f"Currículo Paulista ({ano_escolar} - {periodo_bimestre}º Bimestre - {componente_curricular}): "
        f"desenvolvimento das habilidades estruturantes do bimestre em {componente_curricular}, "
        f"priorizando compreensão conceitual, resolução de situações-problema práticas, leitura e "
        f"interpretação multimodal alinhadas à turma regular de {nome_aluno}."
    )

    opcoes_por_sub: dict[str, list[str]] = {
        "1.1": [
            f"Habilidades consolidadas: {nome_aluno} resolve situações-problema de {componente_curricular} compatíveis com o {ano_escolar} com autonomia e bom raciocínio lógico.",
            f"Aprofundamento por interesse: demonstra facilidade em reconhecer padrões e engaja-se com maior profundidade quando {componente_curricular} se conecta a temas de seu interesse.",
            f"Em desenvolvimento: participa das tarefas de {componente_curricular} quando organizadas em etapas claras e sequenciais.",
            f"Com mediação pontual: realiza registros e atividades de {componente_curricular} com enunciados objetivos e exemplos estruturados.",
        ],
        "1.2": [
            f"Organizar os enunciados de {componente_curricular} de forma objetiva e dividir as tarefas do {periodo_bimestre}º bimestre em etapas sequenciais claras.",
            f"Ajustar a extensão das atividades (menos itens repetitivos, mantendo o nível conceitual do {ano_escolar}) e oferecer tempo flexível quando necessário.",
            f"Conectar as habilidades essenciais de {componente_curricular} do Currículo Paulista a situações práticas, padrões lógicos e temas de interesse do estudante.",
            f"Priorizar as habilidades estruturantes do {ano_escolar} com critérios explícitos de resolução passo a passo.",
        ],
        "1.3": [
            f"Com uso de roteiros estruturados, apoio visual passo a passo e linguagem direta e objetiva nas aulas de {componente_curricular}.",
            f"Por meio de recursos digitais, simuladores interativos ou esquemas gráficos aliados à mediação do professor regente.",
            f"Articulando o planejamento de {componente_curricular} com o professor especializado do AEE, quando houver, para alinhamento das estratégias do bimestre.",
            f"Combinando atividades impressas com organização visual limpa e mediação pontual nos momentos de transição ou maior abstração.",
        ],
        "2.1": [
            f"Explicar o conteúdo de {componente_curricular} em partes menores, utilizando exemplos concretos e situações do cotidiano do estudante.",
            "Garantir previsibilidade na aula com rotina visual, agenda no quadro e instruções claras, objetivas e com apoio visual.",
            "Oferecer mais tempo para a realização das tarefas e retomar os conteúdos estruturantes sempre que necessário.",
            "Realizar mediação ativa nas atividades em dupla ou grupo, favorecendo a interação e o engajamento pela via do DUA.",
        ],
        "2.2": [
            "Oferecer apoio individualizado durante a explicação e execução das atividades mais complexas do componente curricular.",
            "Aplicar adaptação de quantidade (menor número de questões, mantendo o mesmo objetivo pedagógico da turma).",
            "Aplicar adaptação de complexidade (mesma habilidade do Currículo Paulista apresentada em tarefa mais simples e estruturada).",
            "Ajustar a forma de participação e resposta do estudante (permitindo resposta oral, por pictogramas, prática ou digital).",
        ],
        "2.3": [
            "Utilizar esquemas visuais passo a passo, roteiros de aula e organizadores gráficos para dar previsibilidade.",
            "Apresentar enunciados limpos, objetivos e com destaque visual apenas nos comandos principais da tarefa.",
            "Permitir o uso de computador, tablet ou simuladores interativos (como PhET) para experimentação e registro.",
            "Garantir adequação sensorial e previsibilidade do ambiente (assento estratégico, pausa programada e abafadores se necessário).",
        ],
        "3.1": [
            "Observação diária sistematizada no caderno do professor combinada com portfólio das atividades adaptadas do estudante.",
            "Avaliações práticas e provas estruturadas com apoio visual, priorizando a evolução do estudante em relação a ele mesmo.",
            "Lista de verificação (checklist) de habilidades do bimestre e rubricas com critérios claros e graduados de aprendizagem.",
            "Conversas orientadas (avaliação oral), produções visuais/práticas e autoavaliação mediada com apoio visual.",
        ],
        "4.1": [
            f"Vídeos: vídeos educativos curtos com narração objetiva e esquema passo a passo sobre {componente_curricular} ({ano_escolar}), buscar por \"{componente_curricular} {ano_escolar} explicação passo a passo com apoio visual\".",
            f"Simulações e experimentos: simuladores interativos (como PhET) ou experimentos práticos com medidas conectando {componente_curricular} a interesses do aluno ({ano_escolar}), buscar por \"simulador PhET {componente_curricular} {ano_escolar} experimento prático\".",
            f"Jogos: jogos estruturados de lógica, padrões e sequências com regras claras e previsíveis (individual ou com mediador), buscar por \"jogo lógico padrões {componente_curricular} {ano_escolar} estruturado\".",
            f"Atividades: sequências didáticas curtas em etapas graduais com rotina previsível e esquema visual de resolução, buscar por \"atividade {componente_curricular} {ano_escolar} esquema visual passo a passo\".",
        ],
    }

    return conteudo_previsto, opcoes_por_sub


def gerar_roteiro_pei_inicial(
    id_aluno: int,
    nome_aluno: str,
    neurodivergencia: str,
    contexto_aluno: str,
    componente_curricular: str = "Língua Portuguesa",
    ano_escolar: str = "Ensino Fundamental",
    periodo_bimestre: int = 1,
    indice_subpergunta: Optional[int] = None,
) -> PeiInicialResponse:
    """Constrói a resposta do PEI inicial seguindo o Roteiro MetamorphIA.docx."""
    contexto_limpo = (contexto_aluno or "").strip()
    if not contexto_limpo:
        return PeiInicialResponse(
            id_aluno=id_aluno,
            tem_contexto=False,
            precisa_descricao_aluno=True,
            pergunta_inicial_aluno=PERGUNTA_INICIAL_PRIMEIRO_PEI,
            tipo_campo="texto",
            contexto_aluno_utilizado=None,
            indice_subpergunta_atual=None,
            total_subperguntas=0,
            subpergunta_atual=None,
            perguntas=[],
        )

    conteudo_previsto, opcoes_por_sub = _opcoes_padrao_contextualizadas(
        nome_aluno=nome_aluno,
        neurodivergencia=neurodivergencia,
        contexto_aluno=contexto_limpo,
        componente_curricular=componente_curricular,
        ano_escolar=ano_escolar,
        periodo_bimestre=periodo_bimestre,
    )

    settings = get_settings()
    if settings.LLM_PROVIDER.lower() != "mock":
        trechos_curriculo = buscar_documentos_referencia(
            query=f"Currículo Paulista {componente_curricular} {ano_escolar} {periodo_bimestre}º bimestre habilidades",
            n_results=2,
        )
        bloco_curriculo = "\n".join(
            f"- [{t.get('titulo', 'Currículo Paulista')}]: {t.get('texto', '')[:700]}"
            for t in trechos_curriculo
            if t.get("texto")
        )
        secao_curriculo = (
            f"\nTRECHOS DO CURRÍCULO PAULISTA RECUPERADOS PARA {ano_escolar} / {componente_curricular}:\n{bloco_curriculo}\n"
            if bloco_curriculo
            else ""
        )
        prompt_opcoes = (
            f"Você está gerando as opções de resposta para o formulário inicial do PEI (Roteiro MetamorphIA).\n"
            f"Estudante: {nome_aluno} | Neurodivergência: {neurodivergencia}\n"
            f"Contexto do estudante (descrição feita pelo professor): {contexto_limpo}\n"
            f"Componente Curricular: {componente_curricular} | Série/Ano: {ano_escolar} | Bimestre: {periodo_bimestre}º\n"
            f"{secao_curriculo}\n"
            f"Para cada subpergunta abaixo, gere EXATAMENTE 4 possíveis respostas curtas, práticas e ALTAMENTE PERSONALIZADAS para este estudante específico.\n"
            f"REGRAS DE PERSONALIZAÇÃO OBRIGATÓRIAS:\n"
            f"- Baseie TODAS as opções nos interesses, pontos fortes, ritmo de aprendizagem e dificuldades descritos no contexto do estudante, usando linguagem pedagógica profissional.\n"
            f"- ADERÊNCIA ESTRITA À CONDIÇÃO REAL ({neurodivergencia}): NUNCA sugira adaptações de outra deficiência que o estudante NÃO possui (ex.: NÃO sugira 'alto contraste', 'fonte grande/ampliada' ou 'Braille' para estudante com TEA/TDAH sem baixa visão; para TEA priorize previsibilidade, esquema visual passo a passo, instruções objetivas e redução de sobrecarga).\n"
            f"- SÉRIE/ANO REAL: Use EXCLUSIVAMENTE a série/ano '{ano_escolar}' informada acima. PROIBIDO inventar '1º ano' ou outra série diferente.\n"
            f"- RITMO E INTERESSES: Incorpore explicitamente o ritmo de aprendizagem (atividades curtas, em etapas graduais e com rotina previsível) e os interesses do estudante (ex.: se houver interesse em Física, Ciências ou lógica, inclua simuladores como PhET ou experimentos simples com medidas ligados a {componente_curricular}).\n"
            f"- PROIBIDO copiar ou repetir literalmente frases ou palavras brutas escritas pelo professor na descrição inicial (nunca cole o texto do professor entre parênteses).\n"
            f"- PROIBIDO gerar frases genéricas de livro didático que serviriam para qualquer aluno.\n"
            f"- Siga os critérios em vermelho do Roteiro MetamorphIA:\n"
            f"- '1.1': Subpergunta 'O que o estudante já consegue fazer?' (Critérios: habilidades já consolidadas; o que ainda está em desenvolvimento; o que o estudante só realiza com ajuda).\n"
            f"- '1.2': Subpergunta 'O que precisa e é possível de ser ajustado esse bimestre?' (Critérios: simplifica ações, aumenta o tempo de trabalho, divide a habilidade em pequenas etapas, usa recursos acessíveis sem retirar o conteúdo curricular).\n"
            f"- '1.3': Subpergunta 'E como isso será feito?' (Critérios: recursos de acessibilidade, uso de tecnologias assistivas, apoio de profissionais quando houver, adaptações de materiais e linguagem).\n"
            f"- '2.1': Subpergunta 'Quais estratégias pedagógicas você utilizará para que o estudante consiga participar das aulas?' (Critérios: explicar em partes menores, exemplos concretos/cotidiano, mais tempo, mediação em grupo, retomar conteúdos, previsibilidade/rotina visual, instruções claras com apoio visual).\n"
            f"- '2.2': Subpergunta 'Quais intervenções pedagógicas você utilizará para que o estudante consiga participar das aulas?' (Critérios: apoio individual em atividades complexas, adaptação de quantidade, adaptação de complexidade, ajuste da forma de participação oral/pictograma/escrita/digital).\n"
            f"- '2.3': Subpergunta 'Quais recursos de acessibilidade você utilizará para que o estudante consiga participar das aulas?' (Critérios: material ampliado/contrastes/fontes acessíveis, figuras/esquemas/pictogramas/CAA, uso de tablet/computador, fones/abafadores/mobiliário adaptado, tecnologias assistivas).\n"
            f"- '3.1': Subpergunta 'Como você vai avaliar o estudante respeitando seu ritmo, suas necessidades e suas formas de expressão?' (Critérios: observação diária, portfólio, avaliações práticas, checklist de habilidades, registro fotográfico, autoavaliação com apoio visual, conversas orientadas, produções em vídeo/áudio/desenho, rubricas; avaliando o progresso do aluno em relação a ele mesmo).\n"
            f"- '4.1': Subpergunta 'Quais vídeos, livros, jogos, exercícios ou outras atividades você indicará para apoiar e fortalecer o aprendizado do estudante?' (Critérios: 1 opção de Vídeos, 1 opção de Livros/Simulações/Experimentos conectados aos interesses do aluno, 1 opção de Jogos estruturados e 1 opção de Atividades curtas/passo a passo alinhadas ao ritmo de aprendizagem. REGRA CRÍTICA: NUNCA invente títulos fictícios de vídeos do YouTube, livros, canais, autores ou links; em termos de busca, use a série real '{ano_escolar}' e evite termos genéricos como 'acessível' ou 'adaptado' quando não se tratar de deficiência visual/auditiva — prefira 'estruturado', 'passo a passo', 'com apoio visual', 'simulador PhET' ou 'experimento prático').\n\n"
            f"Retorne APENAS um objeto JSON válido com a chave 'conteudo_previsto' (string descrevendo o conteúdo previsto do Currículo Paulista "
            f"para este ano/bimestre em {componente_curricular}) e a chave 'subperguntas' contendo as chaves '1.1', '1.2', '1.3', '2.1', '2.2', '2.3', '3.1', '4.1', "
            f"onde cada chave possui uma lista de EXATAMENTE 4 strings."
        )
        try:
            resposta_llm = gerar_resposta(
                prompt=prompt_opcoes,
                aplicar_guardrails=False,
                system_prompt=PROMPT_SISTEMA_PEI,
            )
            inicio = resposta_llm.find("{")
            fim = resposta_llm.rfind("}")
            if inicio != -1 and fim != -1 and fim > inicio:
                dados_json = json.loads(resposta_llm[inicio : fim + 1])
                if isinstance(dados_json.get("conteudo_previsto"), str) and dados_json["conteudo_previsto"].strip():
                    conteudo_previsto = _limpar_texto_resposta_pei(dados_json["conteudo_previsto"])
                subs_json = dados_json.get("subperguntas", {})
                if isinstance(subs_json, dict):
                    for k in list(opcoes_por_sub.keys()):
                        lista_cand = subs_json.get(k)
                        if isinstance(lista_cand, list):
                            limpas = [
                                _limpar_texto_resposta_pei(str(x))
                                for x in lista_cand
                                if _limpar_texto_resposta_pei(str(x))
                            ]
                            if len(limpas) >= 4:
                                opcoes_por_sub[k] = limpas[:4]
        except Exception:
            pass

    perguntas_roteiro: list[PeiPerguntaGeralRoteiro] = [
        PeiPerguntaGeralRoteiro(
            numero=1,
            pergunta_geral=PERGUNTAS_GERAIS_MODELO[1],
            info_usuario=(
                "O ponto de partida é sempre o Currículo Paulista. O professor utiliza o mesmo currículo da turma, "
                "priorizando as habilidades essenciais (estruturantes) e fazendo adaptações se necessário. "
                "Combine o planejamento com o professor especializado (AEE)."
            ),
            conteudo_previsto_curriculo=conteudo_previsto,
            minimo_subperguntas=3,
            subperguntas=[
                PeiSubperguntaItem(
                    id_subpergunta="1.1",
                    pergunta="O que o estudante já consegue fazer?",
                    opcoes=opcoes_por_sub["1.1"][:4],
                ),
                PeiSubperguntaItem(
                    id_subpergunta="1.2",
                    pergunta="O que precisa e é possível de ser ajustado esse bimestre?",
                    opcoes=opcoes_por_sub["1.2"][:4],
                ),
                PeiSubperguntaItem(
                    id_subpergunta="1.3",
                    pergunta="E como isso será feito?",
                    opcoes=opcoes_por_sub["1.3"][:4],
                ),
            ],
        ),
        PeiPerguntaGeralRoteiro(
            numero=2,
            pergunta_geral=PERGUNTAS_GERAIS_MODELO[2],
            info_usuario="Pelo menos duas das sub-perguntas precisam ser respondidas.",
            minimo_subperguntas=2,
            subperguntas=[
                PeiSubperguntaItem(
                    id_subpergunta="2.1",
                    pergunta="Quais estratégias pedagógicas (como apresentar, explicar e organizar a aula) você utilizará?",
                    opcoes=opcoes_por_sub["2.1"][:4],
                    alternativas_fixas=["Não irei utilizar estratégias pedagógicas", "Outros"],
                ),
                PeiSubperguntaItem(
                    id_subpergunta="2.2",
                    pergunta="Quais intervenções pedagógicas (mediação direta, ajustes de tarefa ou forma de resposta) você aplicará durante a atividade?",
                    opcoes=opcoes_por_sub["2.2"][:4],
                    alternativas_fixas=["Não irei utilizar intervenções pedagógicas", "Outros"],
                ),
                PeiSubperguntaItem(
                    id_subpergunta="2.3",
                    pergunta="Quais recursos de acessibilidade você utilizará para que o estudante consiga participar das aulas?",
                    opcoes=opcoes_por_sub["2.3"][:4],
                    alternativas_fixas=["Não irei utilizar recursos de acessibilidade", "Outros"],
                ),
            ],
        ),
        PeiPerguntaGeralRoteiro(
            numero=3,
            pergunta_geral=PERGUNTAS_GERAIS_MODELO[3],
            info_usuario="A avaliação deve mostrar o progresso do estudante em relação a ele mesmo.",
            minimo_subperguntas=1,
            subperguntas=[
                PeiSubperguntaItem(
                    id_subpergunta="3.1",
                    pergunta="Como você vai avaliar o estudante respeitando seu ritmo, suas necessidades e suas formas de expressão?",
                    opcoes=opcoes_por_sub["3.1"][:4],
                ),
            ],
        ),
        PeiPerguntaGeralRoteiro(
            numero=4,
            pergunta_geral=PERGUNTAS_GERAIS_MODELO[4],
            info_usuario="Indique recursos pedagógicos (vídeos, livros, jogos, atividades e exercícios) adaptados ao componente curricular.",
            minimo_subperguntas=1,
            subperguntas=[
                PeiSubperguntaItem(
                    id_subpergunta="4.1",
                    pergunta="Quais vídeos, livros, jogos, exercícios ou outras atividades você indicará para apoiar e fortalecer o aprendizado do estudante?",
                    opcoes=opcoes_por_sub["4.1"][:4],
                ),
            ],
        ),
    ]

    todas_subs: list[PeiSubperguntaItem] = [
        sub for p in perguntas_roteiro for sub in p.subperguntas
    ]
    idx_atual = indice_subpergunta if indice_subpergunta is not None else 0
    sub_atual = (
        todas_subs[idx_atual]
        if 0 <= idx_atual < len(todas_subs)
        else (todas_subs[0] if todas_subs else None)
    )

    return PeiInicialResponse(
        id_aluno=id_aluno,
        tem_contexto=True,
        precisa_descricao_aluno=False,
        pergunta_inicial_aluno=None,
        tipo_campo=None,
        contexto_aluno_utilizado=contexto_limpo,
        indice_subpergunta_atual=idx_atual if 0 <= idx_atual < len(todas_subs) else 0,
        total_subperguntas=len(todas_subs),
        subpergunta_atual=sub_atual,
        perguntas=perguntas_roteiro,
    )


def _classificar_pergunta_geral(item: PeiRespostaItem, indice: int) -> int:
    if item.numero_pergunta_geral in (1, 2, 3, 4):
        return int(item.numero_pergunta_geral)

    texto_p = item.pergunta.lower().strip()
    if texto_p.startswith("1") or any(
        k in texto_p
        for k in (
            "já consegue fazer",
            "ja consegue fazer",
            "ajustado esse bimestre",
            "como isso será feito",
            "como isso sera feito",
            "conteúdos e habilidades",
            "conteudos e habilidades",
            "currículo",
            "curriculo",
        )
    ):
        return 1
    if texto_p.startswith("2") or any(
        k in texto_p
        for k in (
            "estratégias pedagógicas",
            "estrategias pedagogicas",
            "intervenções pedagógicas",
            "intervencoes pedagogicas",
            "recursos de acessibilidade",
            "participar das aulas",
        )
    ):
        return 2
    if texto_p.startswith("3") or any(
        k in texto_p
        for k in (
            "instrumentos serão utilizados",
            "como você vai avaliar",
            "como voce vai avaliar",
            "avaliar o estudante",
            "acompanhar o aprendizado",
        )
    ):
        return 3
    if texto_p.startswith("4") or any(
        k in texto_p
        for k in (
            "vídeos",
            "videos",
            "livros",
            "jogos",
            "exercícios",
            "exercicios",
            "suplementar",
            "complementar",
        )
    ):
        return 4

    return (indice % 4) + 1


def gerar_respostas_gerais_pei(
    nome_aluno: str,
    idade_aluno: Optional[int],
    neurodivergencia: str,
    contexto_aluno: str,
    respostas_subperguntas: list[PeiRespostaItem],
    componente_curricular: str = "Língua Portuguesa",
    ano_escolar: str = "Ensino Fundamental",
    periodo_bimestre: int = 1,
    contexto_rag: str = "",
    fontes_docs: Optional[list[dict[str, Any]]] = None,
) -> dict[int, str]:
    agrupadas: dict[int, list[str]] = {1: [], 2: [], 3: [], 4: []}
    for idx, item in enumerate(respostas_subperguntas):
        eh_inj_p, _ = detectar_prompt_injection(item.pergunta)
        eh_inj_r, _ = detectar_prompt_injection(item.resposta)
        if eh_inj_p or eh_inj_r:
            continue
        perg_limpa, _ = sanitizar_dados_sensiveis(item.pergunta)
        resp_limpa, _ = sanitizar_dados_sensiveis(item.resposta)
        num_geral = _classificar_pergunta_geral(item, idx)
        agrupadas[num_geral].append(f"- {perg_limpa}: {resp_limpa}")

    todos_dados_brutos = "\n".join(
        f"- {sanitizar_dados_sensiveis(r.pergunta)[0]}: {sanitizar_dados_sensiveis(r.resposta)[0]}"
        for r in respostas_subperguntas
    )

    dados_p1 = "\n".join(agrupadas[1]) or "Nenhuma resposta específica informada pelo professor para esta seção."
    dados_p2 = "\n".join(agrupadas[2]) or "Nenhuma resposta específica informada pelo professor para esta seção."
    dados_p3 = "\n".join(agrupadas[3]) or "Nenhuma resposta específica informada pelo professor para esta seção."
    dados_p4 = "\n".join(agrupadas[4]) or "Nenhuma resposta específica informada pelo professor para esta seção."

    def _montar_fallback_secao(titulo: str, respostas_secao: list[str], complemento: str) -> str:
        if respostas_secao:
            itens = "\n".join(f"• {r.lstrip('- ').strip()}" for r in respostas_secao)
            return f"{titulo}\n{itens}"
        if todos_dados_brutos:
            return f"{titulo}\n• {complemento}"
        return f"{titulo}\n• A definir com base no acompanhamento pedagógico do estudante."

    respostas_padrao: dict[int, str] = {
        1: _montar_fallback_secao(
            f"Conteúdos e Habilidades do Currículo Paulista ({ano_escolar} - {periodo_bimestre}º Bimestre - {componente_curricular}) para {nome_aluno} ({neurodivergencia}):",
            agrupadas[1],
            "Planejamento alinhado às respostas gerais informadas pelo professor.",
        ),
        2: _montar_fallback_secao(
            f"Estratégias, intervenções pedagógicas e recursos de acessibilidade definidos pelo professor para {nome_aluno}:",
            agrupadas[2],
            "Ações pedagógicas definidas conforme as respostas informadas pelo professor.",
        ),
        3: _montar_fallback_secao(
            f"Acompanhamento e avaliação inclusiva da aprendizagem de {nome_aluno}:",
            agrupadas[3],
            "Avaliação processual alinhada ao progresso individual do estudante e às respostas do professor.",
        ),
        4: _montar_fallback_secao(
            f"Recursos e atividades complementares indicados pelo professor para {nome_aluno} em {componente_curricular}:",
            agrupadas[4],
            "Recursos selecionados conforme as respostas informadas pelo professor.",
        ),
    }

    settings = get_settings()
    if settings.LLM_PROVIDER.lower() != "mock":
        prompt_geral = (
            f"Você está redigindo as 4 respostas finais do Plano Educacional Individualizado (PEI - Anexo IV Resolução SEDUC nº 129/2025).\n"
            f"Estudante: {nome_aluno} | Idade: {idade_aluno or 'Não informada'} | Neurodivergência: {neurodivergencia}\n"
            f"Componente Curricular: {componente_curricular} | Ano: {ano_escolar} | Bimestre: {periodo_bimestre}º\n"
            f"Contexto do Estudante: {contexto_aluno}\n\n"
            f"DIRETRIZ DE CENTRALIDADE E FIDELIDADE ESTRITA ÀS RESPOSTAS DO PROFESSOR:\n"
            f"- Baseie cada resposta geral ESTRITAMENTE no que o professor selecionou/respondeu abaixo e no contexto real do estudante.\n"
            f"- PROIBIDO inventar, acrescentar ou presumir habilidades, conteúdos, adaptações, recursos, instrumentos ou série/ano que NÃO foram escolhidos/digitados pelo professor (ex.: se o professor NÃO citou coordenação motora grossa, percepção espacial, linguagem corporal, alto contraste, fonte ampliada, abafadores/protetores auriculares, jogos, livros, planilha diária ou mediação em grupo, é PROIBIDO incluir esses itens).\n"
            f"- FIDELIDADE À RESPOSTA LIVRE: preserve o sentido exato do que o professor digitou sem substituir por outra habilidade ou acrescentar exigências não citadas.\n"
            f"- SÉRIE/ANO E CONTEÚDOS: Use EXCLUSIVAMENTE a série/ano '{ano_escolar}' informada. PROIBIDO inventar '1º ano' ou listar conteúdos específicos (ex.: números de 0 a 20, adição/subtração) que não estejam nas respostas do professor ou nos trechos do Currículo Paulista recuperados para '{ano_escolar}'.\n"
            f"- SEÇÃO 1: Inclua explicitamente o que o estudante já consegue fazer (Pergunta 1.1) abrindo a resposta da Seção 1.\n"
            f"- NÃO fuja do que o professor pediu, NÃO contradiga escolhas dele (ex: se indicou que não usará algo) e NÃO despeje listas genéricas.\n"
            f"- Gere a resposta APENAS para cada uma das 4 Perguntas Gerais (NÃO repita as subperguntas como perguntas no texto final).\n"
            f"- FORMATAÇÃO: escreva em texto corrido e tópicos simples ('• '). É PROIBIDO usar tabelas (Markdown ou quadros), marcações Markdown ('**', '###') e é PROIBIDO incluir citações de fonte ('*Fonte base:...*', 'Fonte:', 'Referências:').\n\n"
            f"Pergunta Geral 1: {PERGUNTAS_GERAIS_MODELO[1]}\n"
            f"Respostas do Professor (Seção 1):\n{dados_p1}\n\n"
            f"Pergunta Geral 2: {PERGUNTAS_GERAIS_MODELO[2]}\n"
            f"Respostas do Professor (Seção 2):\n{dados_p2}\n\n"
            f"Pergunta Geral 3: {PERGUNTAS_GERAIS_MODELO[3]}\n"
            f"Respostas do Professor (Seção 3):\n{dados_p3}\n\n"
            f"Pergunta Geral 4: {PERGUNTAS_GERAIS_MODELO[4]}\n"
            f"Respostas do Professor (Seção 4):\n{dados_p4}\n\n"
            f"Retorne APENAS um JSON válido com as chaves '1', '2', '3' e '4', contendo o texto limpo (sem tabelas, sem Markdown e sem fontes) de cada resposta geral."
        )
        try:
            texto_ia = gerar_resposta(
                prompt=prompt_geral,
                contexto_rag=contexto_rag,
                fontes_referencia=None,
                aplicar_guardrails=False,
                system_prompt=PROMPT_SISTEMA_PEI,
            )
            ini = texto_ia.find("{")
            fim = texto_ia.rfind("}")
            if ini != -1 and fim != -1 and fim > ini:
                parsed = json.loads(texto_ia[ini : fim + 1])
                for num in (1, 2, 3, 4):
                    val = parsed.get(str(num)) or parsed.get(f"pergunta_{num}")
                    if isinstance(val, str) and len(val.strip()) > 20:
                        limpo = _limpar_texto_resposta_pei(sanitizar_dados_sensiveis(val.strip())[0])
                        if len(limpo) > 20:
                            respostas_padrao[num] = limpo
        except Exception:
            pass

    return respostas_padrao


def formatar_texto_pei_completo(
    nome_aluno: str,
    nome_professor_regente: str,
    nome_professor_especializado: str,
    componente_curricular: str,
    periodo_bimestre: int,
    respostas_gerais: dict[int, str],
) -> str:
    """Formata a representação textual espelhada do documento PEI (sem as instruções azuis)."""
    marcadores = " ".join(
        f"({'X' if b == periodo_bimestre else ' '}) {b}º Bimestre" for b in (1, 2, 3, 4)
    )
    return (
        "PLANO EDUCACIONAL ESPECIALIZADO - PEI\n"
        "ANEXO IV - RESOLUÇÃO SEDUC Nº 129/2025\n\n"
        f"Nome do Estudante: {nome_aluno}\n"
        f"Nome do Professor Regente: {nome_professor_regente}\n"
        f"Nome do Professor Especializado da Educação Especial: {nome_professor_especializado}\n"
        f"Componente Curricular: {componente_curricular}\n"
        f"Período: {marcadores}\n\n"
        f"1. {PERGUNTAS_GERAIS_MODELO[1]}\n"
        f"{respostas_gerais[1]}\n\n"
        f"2. {PERGUNTAS_GERAIS_MODELO[2]}\n"
        f"{respostas_gerais[2]}\n\n"
        f"3. {PERGUNTAS_GERAIS_MODELO[3]}\n"
        f"{respostas_gerais[3]}\n\n"
        f"4. {PERGUNTAS_GERAIS_MODELO[4]}\n"
        f"{respostas_gerais[4]}\n"
    )


def _criar_paragrafo_resposta_docx(texto_linha: str, w_ns: str) -> ET.Element:
    """Cria um elemento <w:p> formatado com borda e fonte Calibri 12pt preta (sem fundo azul)."""
    p = ET.Element(f"{{{w_ns}}}p")
    pPr = ET.SubElement(p, f"{{{w_ns}}}pPr")

    pBdr = ET.SubElement(pPr, f"{{{w_ns}}}pBdr")
    for lado, space in (("top", "1"), ("left", "4"), ("bottom", "1"), ("right", "4"), ("between", "1")):
        b = ET.SubElement(pBdr, f"{{{w_ns}}}{lado}")
        b.set(f"{{{w_ns}}}color", "000000")
        b.set(f"{{{w_ns}}}space", space)
        b.set(f"{{{w_ns}}}sz", "4")
        b.set(f"{{{w_ns}}}val", "single")

    spacing = ET.SubElement(pPr, f"{{{w_ns}}}spacing")
    spacing.set(f"{{{w_ns}}}after", "80")
    spacing.set(f"{{{w_ns}}}line", "240")
    spacing.set(f"{{{w_ns}}}lineRule", "auto")

    jc = ET.SubElement(pPr, f"{{{w_ns}}}jc")
    jc.set(f"{{{w_ns}}}val", "both")

    r = ET.SubElement(p, f"{{{w_ns}}}r")
    rPr = ET.SubElement(r, f"{{{w_ns}}}rPr")
    rFonts = ET.SubElement(rPr, f"{{{w_ns}}}rFonts")
    for attr in ("ascii", "cs", "eastAsia", "hAnsi"):
        rFonts.set(f"{{{w_ns}}}{attr}", "Calibri")
    color = ET.SubElement(rPr, f"{{{w_ns}}}color")
    color.set(f"{{{w_ns}}}val", "000000")
    sz = ET.SubElement(rPr, f"{{{w_ns}}}sz")
    sz.set(f"{{{w_ns}}}val", "24")
    szCs = ET.SubElement(rPr, f"{{{w_ns}}}szCs")
    szCs.set(f"{{{w_ns}}}val", "24")

    t = ET.SubElement(r, f"{{{w_ns}}}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = texto_linha
    return p


def _definir_texto_paragrafo_identificacao(
    p_elem: ET.Element, rotulo: str, valor: str, w_ns: str
) -> None:
    runs = p_elem.findall(f"{{{w_ns}}}r")
    for r in runs:
        p_elem.remove(r)

    r_label = ET.SubElement(p_elem, f"{{{w_ns}}}r")
    rPr_label = ET.SubElement(r_label, f"{{{w_ns}}}rPr")
    rFonts = ET.SubElement(rPr_label, f"{{{w_ns}}}rFonts")
    for attr in ("ascii", "cs", "eastAsia", "hAnsi"):
        rFonts.set(f"{{{w_ns}}}{attr}", "Calibri")
    b = ET.SubElement(rPr_label, f"{{{w_ns}}}b")
    b.set(f"{{{w_ns}}}val", "1")
    bCs = ET.SubElement(rPr_label, f"{{{w_ns}}}bCs")
    bCs.set(f"{{{w_ns}}}bCs", "1")
    sz = ET.SubElement(rPr_label, f"{{{w_ns}}}sz")
    sz.set(f"{{{w_ns}}}val", "24")
    szCs = ET.SubElement(rPr_label, f"{{{w_ns}}}szCs")
    szCs.set(f"{{{w_ns}}}val", "24")
    t_label = ET.SubElement(r_label, f"{{{w_ns}}}t")
    t_label.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_label.text = f"{rotulo} "

    if valor:
        r_val = ET.SubElement(p_elem, f"{{{w_ns}}}r")
        rPr_val = ET.SubElement(r_val, f"{{{w_ns}}}rPr")
        rFonts_v = ET.SubElement(rPr_val, f"{{{w_ns}}}rFonts")
        for attr in ("ascii", "cs", "eastAsia", "hAnsi"):
            rFonts_v.set(f"{{{w_ns}}}{attr}", "Calibri")
        sz_v = ET.SubElement(rPr_val, f"{{{w_ns}}}sz")
        sz_v.set(f"{{{w_ns}}}val", "24")
        szCs_v = ET.SubElement(rPr_val, f"{{{w_ns}}}szCs")
        szCs_v.set(f"{{{w_ns}}}val", "24")
        t_val = ET.SubElement(r_val, f"{{{w_ns}}}t")
        t_val.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t_val.text = valor


def montar_docx_pei(
    nome_aluno: str,
    respostas_gerais: dict[int, str],
    nome_professor_regente: str = "",
    nome_professor_especializado: str = "",
    componente_curricular: str = "",
    periodo_bimestre: int = 1,
) -> bytes:
    caminho_modelo = _localizar_modelo_docx()
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": w_ns}

    with zipfile.ZipFile(caminho_modelo, "r") as zin:
        xml_bytes = zin.read("word/document.xml")
        arquivos_zip = {name: zin.read(name) for name in zin.namelist() if name != "word/document.xml"}

    tree = ET.fromstring(xml_bytes)
    body = tree.find("w:body", ns)
    if body is None:
        raise RuntimeError("Estrutura inválida no word/document.xml do modelo PEI.")

    elementos = list(body)
    novos_elementos: list[ET.Element] = []

    bimestre_num = periodo_bimestre if periodo_bimestre in (1, 2, 3, 4) else 1
    texto_periodo = (
        f"({'X' if bimestre_num == 1 else ' '}) 1º Bimestre "
        f"({'X' if bimestre_num == 2 else ' '}) 2º bimestre "
        f"({'X' if bimestre_num == 3 else ' '}) 3º Bimestre "
        f"({'X' if bimestre_num == 4 else ' '}) 4º Bimestre"
    )

    bloco_azul_atual = 0
    em_bloco_azul = False

    for el in elementos:
        tag = el.tag.split("}")[-1]
        if tag != "p":
            novos_elementos.append(el)
            continue

        shd = el.find("./w:pPr/w:shd", ns)
        fill = shd.attrib.get(f"{{{w_ns}}}fill", "").lower() if shd is not None else ""
        cores = {
            c.attrib.get(f"{{{w_ns}}}val", "").lower()
            for c in el.findall(".//w:color", ns)
        }
        eh_paragrafo_azul = (fill == "dae9f7") or ("153d63" in cores)

        if eh_paragrafo_azul:
            if not em_bloco_azul:
                em_bloco_azul = True
                bloco_azul_atual += 1
                texto_resp = respostas_gerais.get(bloco_azul_atual, "")
                linhas = [ln.strip() for ln in texto_resp.splitlines() if ln.strip()]
                if not linhas:
                    linhas = [texto_resp.strip() or "Planejamento registrado conforme diretrizes do bimestre."]
                for linha in linhas:
                    novos_elementos.append(_criar_paragrafo_resposta_docx(linha, w_ns))
            # Ignora o parágrafo azul para que nunca apareça no arquivo final
            continue

        em_bloco_azul = False
        texto_p = "".join(t.text for t in el.findall(".//w:t", ns) if t.text).strip()

        # Remove o parágrafo órfão "bimestre?" da Pergunta 1 (já unificado na linha da Pergunta 1)
        if texto_p == "bimestre?":
            continue

        el_copia = copy.deepcopy(el)
        if texto_p.startswith("Quais conteúdos e habilidades do Currículo da Rede Estadual Paulista"):
            # Unifica o texto completo da Pergunta 1 e usa o mesmo numId="6" das Perguntas 2, 3 e 4 para numerar 1, 2, 3 e 4
            num_id_el = el_copia.find("./w:pPr/w:numPr/w:numId", ns)
            if num_id_el is not None:
                num_id_el.set(f"{{{w_ns}}}val", "6")
            ts = el_copia.findall(".//w:t", ns)
            if ts:
                ts[0].text = PERGUNTAS_GERAIS_MODELO[1]
                for t_extra in ts[1:]:
                    t_extra.text = ""
        elif texto_p.startswith("Nome do Estudante:"):
            _definir_texto_paragrafo_identificacao(el_copia, "Nome do Estudante:", nome_aluno, w_ns)
        elif texto_p.startswith("Nome do Professor Regente:"):
            _definir_texto_paragrafo_identificacao(
                el_copia, "Nome do Professor Regente:", nome_professor_regente, w_ns
            )
        elif texto_p.startswith("Nome do Professor Especializado da Educação Especial:"):
            _definir_texto_paragrafo_identificacao(
                el_copia,
                "Nome do Professor Especializado da Educação Especial:",
                nome_professor_especializado,
                w_ns,
            )
        elif texto_p.startswith("Componente Curricular:"):
            _definir_texto_paragrafo_identificacao(
                el_copia, "Componente Curricular:", componente_curricular, w_ns
            )
        elif texto_p.startswith("Período:"):
            _definir_texto_paragrafo_identificacao(el_copia, "Período:", texto_periodo, w_ns)
        elif texto_p.startswith("Professor de Componente Curricular:") and componente_curricular:
            _definir_texto_paragrafo_identificacao(
                el_copia, "Professor de Componente Curricular:", componente_curricular, w_ns
            )

        novos_elementos.append(el_copia)

    body[:] = novos_elementos
    novo_xml = ET.tostring(tree, encoding="utf-8", xml_declaration=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("word/document.xml", novo_xml)
        for nome_arq, conteudo_bytes in arquivos_zip.items():
            zout.writestr(nome_arq, conteudo_bytes)

    return buffer.getvalue()


async def _resolver_ano_escolar_turma(
    aluno: Any,
    ano_informado: Optional[str],
    token: Optional[str],
) -> str:
    ano_limpo = (ano_informado or "").strip()
    if ano_limpo and ano_limpo.lower() not in ("ensino fundamental", "string", "none", "null"):
        return ano_limpo
    if getattr(aluno, "id_turma", None):
        try:
            turma = await obter_turma(int(aluno.id_turma), token=token)
            serie = (getattr(turma, "serie_ano", None) or "").strip()
            if serie:
                return serie
            nome_t = (getattr(turma, "nome_turma", None) or "").strip()
            if nome_t:
                return nome_t
        except Exception:
            pass
    return ano_limpo or "Ensino Fundamental"


@router.post("/alunos/{id_aluno}/pei/inicial", response_model=PeiInicialResponse)
async def criar_pei_inicial(
    id_aluno: int,
    payload: Optional[PeiInicialRequest] = None,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PeiInicialResponse:
    """Inicia a elaboração do PEI com base no Roteiro MetamorphIA.

    - Apenas na 1ª vez do aluno (quando perfil_ia e desc_aluno estiverem vazios) e nenhuma descrição
      tiver sido enviada no body, retorna a pergunta pedindo que o professor descreva o estudante.
    - Nas vezes seguintes (quando o aluno já possui perfil_ia/desc_aluno) ou após o envio da descrição,
      gera todas as subperguntas do roteiro com 4 alternativas de múltipla escolha cada.
    """
    validar_permissao_chat_usuario(usuario_autenticado)
    req = payload or PeiInicialRequest()
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado na base de dados.")

    desc_bruta = (req.descricao_aluno or "").strip()
    if desc_bruta.lower() in ("string", "none", "null"):
        desc_bruta = ""

    if desc_bruta:
        desc_limpa, _ = sanitizar_dados_sensiveis(desc_bruta)
        try:
            await atualizar_descricao_aluno(id_aluno, desc_aluno=desc_limpa, token=token_jwt)
        except Exception:
            pass
    else:
        desc_limpa = None

    contexto_aluno = _obter_contexto_consolidado_aluno(
        desc_aluno=aluno.desc_aluno,
        perfil_ia=aluno.perfil_ia,
        descricao_enviada=desc_limpa,
    )

    if req.componente_curricular:
        try:
            await atualizar_status_pei_disciplina_db(
                id_aluno=id_aluno,
                disciplina=req.componente_curricular,
                status_pei="em_andamento",
                token=token_jwt,
            )
        except Exception:
            pass

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(aluno.neurodiv_aluno)
    ano_escolar_efetivo = await _resolver_ano_escolar_turma(aluno, req.ano_escolar, token_jwt)
    componente_normalizado = _normalizar_nome_componente(req.componente_curricular)

    return await asyncio.to_thread(
        gerar_roteiro_pei_inicial,
        id_aluno=id_aluno,
        nome_aluno=nome_aluno_limpo,
        neurodivergencia=neuro_limpa,
        contexto_aluno=contexto_aluno,
        componente_curricular=componente_normalizado,
        ano_escolar=ano_escolar_efetivo,
        periodo_bimestre=req.periodo_bimestre or 1,
        indice_subpergunta=req.indice_subpergunta,
    )


async def _processar_criacao_pei(
    id_aluno: int,
    payload: PeiCriarRequest,
    usuario_autenticado: dict[str, Any],
) -> tuple[PeiCriarResponse, bytes]:
    from api.routes.dashboard_aluno import gerar_contexto_sintetizado_aluno

    validar_permissao_chat_usuario(usuario_autenticado)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado na base de dados.")

    contexto_aluno = _obter_contexto_consolidado_aluno(
        desc_aluno=aluno.desc_aluno,
        perfil_ia=aluno.perfil_ia,
        descricao_enviada=payload.descricao_aluno,
    )

    componente, _ = sanitizar_dados_sensiveis(_normalizar_nome_componente(payload.componente_curricular))
    ano_escolar_efetivo = await _resolver_ano_escolar_turma(aluno, payload.ano_escolar, token_jwt)
    ano_escolar, _ = sanitizar_dados_sensiveis(ano_escolar_efetivo)
    bimestre = payload.periodo_bimestre if payload.periodo_bimestre in (1, 2, 3, 4) else 1

    fontes_docs = await asyncio.to_thread(
        buscar_documentos_referencia,
        query=f"Currículo Paulista {componente} {ano_escolar} {bimestre}º bimestre habilidades PEI adaptações curriculares DUA",
        n_results=3,
    )
    textos_rag = "\n\n".join(f"[{f['titulo']}]: {f['texto']}" for f in fontes_docs)

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(aluno.neurodiv_aluno)
    prof_regente, _ = sanitizar_dados_sensiveis(
        payload.nome_professor_regente or usuario_autenticado.get("nome_prof", "") or ""
    )
    prof_esp, _ = sanitizar_dados_sensiveis(payload.nome_professor_especializado or "")

    respostas_gerais_int = await asyncio.to_thread(
        gerar_respostas_gerais_pei,
        nome_aluno=nome_aluno_limpo,
        idade_aluno=aluno.idade_aluno,
        neurodivergencia=neuro_limpa,
        contexto_aluno=contexto_aluno,
        respostas_subperguntas=payload.respostas,
        componente_curricular=componente,
        ano_escolar=ano_escolar,
        periodo_bimestre=bimestre,
        contexto_rag=textos_rag,
        fontes_docs=fontes_docs,
    )

    documento_gerado = formatar_texto_pei_completo(
        nome_aluno=nome_aluno_limpo,
        nome_professor_regente=prof_regente,
        nome_professor_especializado=prof_esp,
        componente_curricular=componente,
        periodo_bimestre=bimestre,
        respostas_gerais=respostas_gerais_int,
    )

    docx_bytes = montar_docx_pei(
        nome_aluno=nome_aluno_limpo,
        respostas_gerais=respostas_gerais_int,
        nome_professor_regente=prof_regente,
        nome_professor_especializado=prof_esp,
        componente_curricular=componente,
        periodo_bimestre=bimestre,
    )

    # Sintetiza imediatamente o perfil_ia consolidado do aluno a partir da descrição + PEI elaborado
    if payload.descricao_aluno and not aluno.desc_aluno:
        aluno.desc_aluno = sanitizar_dados_sensiveis(payload.descricao_aluno)[0]
    perfil_sintetizado, _, indicadores_gerados, metas_geradas = await gerar_contexto_sintetizado_aluno(
        aluno=aluno,
        contexto_pei=documento_gerado,
        sincronizar_db=False,
        token=token_jwt,
    )

    pei_id = str(uuid.uuid4())
    data_iso = datetime.now(timezone.utc).isoformat()
    nome_arquivo_seguro = "".join(
        c if c.isalnum() or c in ("_", "-") else "_" for c in nome_aluno_limpo
    ).strip("_") or f"aluno_{id_aluno}"
    arquivo_nome = f"PEI_{nome_arquivo_seguro}_{bimestre}Bim_2026.docx"
    docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
    pei_valido_ate: Optional[str] = None

    if not payload.persistido_pelo_gateway:
        try:
            meta_db = await salvar_pei_arquivo_db(
                id_aluno=id_aluno,
                pei_id=pei_id,
                arquivo_nome=arquivo_nome,
                documento_docx_base64=docx_b64,
                componente_curricular=componente,
                perfil_ia=perfil_sintetizado,
                desc_aluno=payload.descricao_aluno,
                indicadores_desenvolvimento=indicadores_gerados,
                metas_bimestre=metas_geradas,
                validade_dias=60,
                token=token_jwt,
            )
            pei_valido_ate = meta_db.get("pei_valido_ate")
        except Exception as exc:
            levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")

    # Indexa o PEI desta disciplina no pgvector do aluno (preservando os PEIs de outras disciplinas)
    chunks_pei = dividir_texto_pei_em_chunks(
        texto_pei=documento_gerado,
        disciplina=componente,
        nome_aluno=nome_aluno_limpo,
    )
    if chunks_pei:
        await indexar_contexto_aluno_pgvector(
            id_aluno=id_aluno,
            origem="pei",
            textos=chunks_pei,
            disciplina=componente,
            pei_id=pei_id,
            substituir_origem_disciplina=True,
            token=token_jwt,
        )

    from api.routes.dashboard_aluno import calcular_status_multi_pei_usuario

    tags_multi_pei = usuario_autenticado.get("tags") or []
    if aluno.id_turma:
        try:
            turma_pei = await obter_turma(aluno.id_turma, token=token_jwt)
            if getattr(turma_pei, "disciplinas", None):
                tags_multi_pei = list(turma_pei.disciplinas)
        except Exception:
            pass

    status_multi = calcular_status_multi_pei_usuario(
        aluno=aluno,
        tags_usuario=tags_multi_pei,
        disciplina_recem_concluida=componente,
    )

    resposta_obj = PeiCriarResponse(
        pei_id=pei_id,
        documento_gerado=documento_gerado,
        data=data_iso,
        formato="docx",
        arquivo_nome=arquivo_nome,
        respostas_gerais={
            PERGUNTAS_GERAIS_MODELO[k]: v for k, v in respostas_gerais_int.items()
        },
        documento_docx_base64=docx_b64,
        download_url=f"/alunos/{id_aluno}/pei/{pei_id}/docx",
        perfil_atualizado=perfil_sintetizado,
        indicadores_desenvolvimento=[IndicadorDesenvolvimentoItem(**i) for i in indicadores_gerados],
        metas_bimestre=[MetaBimestreItem(**m) for m in metas_geradas],
        pei_feito=True,
        pei_valido_ate=pei_valido_ate,
        todos_peis_concluidos=status_multi["todos_concluidos"],
        disciplinas_pendentes=status_multi["pendentes"],
    )
    return resposta_obj, docx_bytes


@router.post("/alunos/{id_aluno}/pei", response_model=PeiCriarResponse)
async def criar_pei(
    id_aluno: int,
    payload: PeiCriarRequest,
    formato: str = Query(
        default="json",
        description="Use 'docx' para baixar diretamente o arquivo .docx ou 'json' para receber os metadados e o .docx em base64.",
    ),
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
):
    """Gera o PEI do aluno seguindo o modelo 2026 (.docx), salva no Supabase Storage e atualiza o perfil_ia."""
    resposta_obj, docx_bytes = await _processar_criacao_pei(
        id_aluno=id_aluno,
        payload=payload,
        usuario_autenticado=usuario_autenticado,
    )
    if formato.lower() == "docx":
        return Response(
            content=docx_bytes,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{resposta_obj.arquivo_nome}"',
                "X-Pei-Id": resposta_obj.pei_id,
            },
        )
    return resposta_obj


@router.post("/alunos/{id_aluno}/pei/docx")
async def criar_pei_docx(
    id_aluno: int,
    payload: PeiCriarRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Gera, persiste no Supabase Storage + API-DB e retorna diretamente o arquivo .docx do PEI."""
    resposta_obj, docx_bytes = await _processar_criacao_pei(
        id_aluno=id_aluno,
        payload=payload,
        usuario_autenticado=usuario_autenticado,
    )
    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{resposta_obj.arquivo_nome}"',
            "X-Pei-Id": resposta_obj.pei_id,
        },
    )


@router.get("/alunos/{id_aluno}/pei/{pei_id}/docx")
async def baixar_pei_docx(
    id_aluno: int,
    pei_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa o arquivo .docx de um PEI já gerado diretamente do Supabase Storage via API-DB (fail-closed)."""
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        docx_bytes, content_disp = await baixar_pei_docx_db(
            id_aluno=id_aluno, pei_id=pei_id, token=token_jwt
        )
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado="PEI não encontrado para este aluno.")

    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disp,
            "X-Pei-Id": pei_id,
        },
    )


@router.get("/alunos/{id_aluno}/pei", response_model=list[PeiResumo])
async def get_peis_aluno(
    id_aluno: int,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> list[PeiResumo]:
    """Lista todos os PEIs registrados para o estudante via API-DB (fail-closed)."""
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        peis = await listar_peis_aluno_db(id_aluno=id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado na base de dados.")
    return [PeiResumo(pei_id=p["pei_id"], data=p.get("data", ""), resumo=p.get("resumo", "")) for p in peis]


@router.get("/alunos/{id_aluno}/pei/{pei_id}", response_model=PeiDetalhe)
async def get_pei_detalhe(
    id_aluno: int,
    pei_id: str,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PeiDetalhe:
    """Busca o conteúdo de um PEI específico salvo no Supabase Storage via API-DB (fail-closed)."""
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        pei = await obter_pei_detalhe_db(id_aluno=id_aluno, pei_id=pei_id, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado="PEI não encontrado para este aluno.")
    return PeiDetalhe(**pei)


@router.post("/alunos/{id_aluno}/pei/importar", response_model=PeiCriarResponse)
async def importar_pei_arquivo(
    id_aluno: int,
    payload: PeiImportarRequest,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PeiCriarResponse:
    """Importa um arquivo de PEI existente (.pdf, .docx, .txt), extrai seu conteúdo,
    sintetiza o perfil_ia, envia o .docx ao Supabase Storage e ativa a validade de 60 dias.
    """
    from api.routes.dashboard_aluno import gerar_contexto_sintetizado_aluno
    from arquivo_utils import decodificar_base64_ou_data_url, extrair_texto_arquivo

    validar_permissao_chat_usuario(usuario_autenticado)
    token_jwt = usuario_autenticado.get("_raw_token")
    try:
        aluno = await obter_aluno(id_aluno, token=token_jwt)
    except Exception as exc:
        levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado na base de dados.")

    raw_bytes = decodificar_base64_ou_data_url(payload.arquivo_base64)
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível decodificar o arquivo enviado.",
        )

    texto_extraido = extrair_texto_arquivo(payload.arquivo_nome, raw_bytes)
    texto_limpo, _ = sanitizar_dados_sensiveis(texto_extraido or f"PEI importado: {payload.arquivo_nome}")

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    componente = payload.componente_curricular or "Língua Portuguesa"
    bimestre = payload.periodo_bimestre if payload.periodo_bimestre in (1, 2, 3, 4) else 1

    # Sempre reconstrói um pacote OpenXML limpo e sanitizado em vez de hospedar bytes brutos enviados pelo usuário
    docx_bytes = montar_docx_pei(
        nome_aluno=nome_aluno_limpo,
        respostas_gerais={
            1: texto_limpo[:1500] or "Conteúdo importado do documento original.",
            2: "Estratégias e recursos importados do PEI do estudante.",
            3: "Avaliação processual e contínua conforme PEI importado.",
            4: "Recursos complementares conforme documento importado.",
        },
        componente_curricular=componente,
        periodo_bimestre=bimestre,
    )

    perfil_sintetizado, _, indicadores_gerados, metas_geradas = await gerar_contexto_sintetizado_aluno(
        aluno=aluno,
        contexto_pei=texto_limpo[:4500],
        sincronizar_db=False,
        token=token_jwt,
    )

    pei_id = str(uuid.uuid4())
    data_iso = datetime.now(timezone.utc).isoformat()
    nome_seguro = "".join(c if c.isalnum() else "_" for c in nome_aluno_limpo).strip("_") or f"aluno_{id_aluno}"
    arquivo_nome = f"PEI_{nome_seguro}_{bimestre}Bim_2026.docx"
    docx_b64 = base64.b64encode(docx_bytes).decode("ascii")
    pei_valido_ate: Optional[str] = None

    if not payload.persistido_pelo_gateway:
        try:
            meta_db = await salvar_pei_arquivo_db(
                id_aluno=id_aluno,
                pei_id=pei_id,
                arquivo_nome=arquivo_nome,
                documento_docx_base64=docx_b64,
                componente_curricular=componente,
                perfil_ia=perfil_sintetizado,
                indicadores_desenvolvimento=indicadores_gerados,
                metas_bimestre=metas_geradas,
                validade_dias=60,
                token=token_jwt,
            )
            pei_valido_ate = meta_db.get("pei_valido_ate")
        except Exception as exc:
            levantar_http_de_erro_db(exc, msg_nao_encontrado=f"Aluno {id_aluno} não encontrado.")

    chunks_imp = dividir_texto_pei_em_chunks(
        texto_pei=texto_limpo,
        disciplina=componente,
        nome_aluno=nome_aluno_limpo,
    )
    if chunks_imp:
        await indexar_contexto_aluno_pgvector(
            id_aluno=id_aluno,
            origem="pei",
            textos=chunks_imp,
            disciplina=componente,
            pei_id=pei_id,
            substituir_origem_disciplina=True,
            token=token_jwt,
        )

    from api.routes.dashboard_aluno import calcular_status_multi_pei_usuario

    tags_multi_pei = usuario_autenticado.get("tags") or []
    if aluno.id_turma:
        try:
            turma_pei = await obter_turma(aluno.id_turma, token=token_jwt)
            if getattr(turma_pei, "disciplinas", None):
                tags_multi_pei = list(turma_pei.disciplinas)
        except Exception:
            pass

    status_multi = calcular_status_multi_pei_usuario(
        aluno=aluno,
        tags_usuario=tags_multi_pei,
        disciplina_recem_concluida=componente,
    )

    return PeiCriarResponse(
        pei_id=pei_id,
        documento_gerado=texto_limpo,
        data=data_iso,
        formato="docx",
        arquivo_nome=arquivo_nome,
        respostas_gerais={
            PERGUNTAS_GERAIS_MODELO[1]: texto_limpo,
        },
        documento_docx_base64=docx_b64,
        download_url=f"/alunos/{id_aluno}/pei/{pei_id}/docx",
        perfil_atualizado=perfil_sintetizado,
        indicadores_desenvolvimento=[IndicadorDesenvolvimentoItem(**i) for i in indicadores_gerados],
        metas_bimestre=[MetaBimestreItem(**m) for m in metas_geradas],
        pei_feito=True,
        pei_valido_ate=pei_valido_ate,
        todos_peis_concluidos=status_multi["todos_concluidos"],
        disciplinas_pendentes=status_multi["pendentes"],
    )
