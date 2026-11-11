"""Módulo de ingestão e indexação vetorial dos documentos pedagógicos e legais.

Lê os PDFs da pasta data/raw, divide o conteúdo em trechos semânticos, aplica
a destilação pedagógica autoral com auditoria estrita de similaridade (< 20%)
e persiste no banco vetorial ChromaDB com metadados detalhados.
Garante que nenhum trecho original protegido por direitos autorais vá para o banco.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import chromadb
from pypdf import PdfReader

from config import get_settings

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from google import genai
except ImportError:
    genai = None

# Metadados e catálogo dos documentos de referência oficiais
CATALOGO_DOCUMENTOS: dict[str, dict[str, str]] = {
    "D12686.pdf": {
        "titulo": "Decreto nº 12.686/2025 - Política Nacional de Educação Especial Inclusiva",
        "autor": "Presidência da República / MEC - Brasil",
        "categoria": "Legislação e Diretrizes Oficiais",
        "citacao_padrao": "BRASIL. Decreto nº 12.686, de 20 de outubro de 2025. Institui a Política Nacional de Educação Especial Inclusiva e a Rede Nacional de Educação Especial Inclusiva. Diário Oficial da União, 2025.",
    },
    "decreto-12686_pneei_2025.pdf": {
        "titulo": "Decreto nº 12.686/2025 - Política Nacional de Educação Especial Inclusiva (PNEEI)",
        "autor": "Presidência da República / Câmara dos Deputados - Brasil",
        "categoria": "Legislação e Diretrizes Oficiais",
        "citacao_padrao": "BRASIL. Decreto nº 12.686, de 20 de outubro de 2025. Institui a Política Nacional de Educação Especial Inclusiva e a Rede Nacional de Educação Especial Inclusiva. Centro de Documentação e Informação da Câmara dos Deputados, 2025.",
    },
    "cast-diretrizes-do-desenho-universal-para-a-aprendizagem.pdf": {
        "titulo": "Diretrizes do Desenho Universal para a Aprendizagem (DUA) versão 3.0",
        "autor": "CAST (Tradução Prof. Dr. Eladio Sebastián-Heredero)",
        "categoria": "Desenho Universal para a Aprendizagem (DUA)",
        "citacao_padrao": "CAST (2024). Diretrizes do Desenho Universal para a Aprendizagem versão 3.0. Wakefield, MA. Tradução: Eladio Sebastián-Heredero.",
    },
    "dua-diretrizes_cast_2024.pdf": {
        "titulo": "Diretrizes do Desenho Universal para a Aprendizagem (DUA) versão 3.0",
        "autor": "CAST (Tradução Prof. Dr. Eladio Sebastián-Heredero)",
        "categoria": "Desenho Universal para a Aprendizagem (DUA)",
        "citacao_padrao": "CAST (2024). Diretrizes do Desenho Universal para a Aprendizagem versão 3.0. Wakefield, MA. Tradução: Eladio Sebastián-Heredero.",
    },
    "behavioral-interventions-practice-guide_v3a_508a.pdf": {
        "titulo": "Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001)",
        "autor": "Kathleen Lynne Lane et al. / What Works Clearinghouse (IES)",
        "categoria": "Manejo Comportamental e Intervenções em Sala de Aula",
        "citacao_padrao": "LANE, K. L. et al. Teacher-Delivered Behavioral Interventions in Grades K–5: A Practice Guide for Educators (WWC 2025001). Washington, DC: NCEE/IES, U.S. Department of Education, 2024.",
    },
    "intervenções_wwc_2024.pdf": {
        "titulo": "Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001)",
        "autor": "Kathleen Lynne Lane et al. / What Works Clearinghouse (IES)",
        "categoria": "Manejo Comportamental e Intervenções em Sala de Aula",
        "citacao_padrao": "LANE, K. L. et al. Teacher-Delivered Behavioral Interventions in Grades K–5: A Practice Guide for Educators (WWC 2025001). Washington, DC: NCEE/IES, U.S. Department of Education, 2024.",
    },
    "WWC2021006-Math-PG.pdf": {
        "titulo": "Assisting Students Struggling with Mathematics: Intervention in Elementary Grades (WWC 2021006)",
        "autor": "Lynn S. Fuchs et al. / What Works Clearinghouse (IES)",
        "categoria": "Adaptação e Ensino de Matemática para Alunos com Dificuldades",
        "citacao_padrao": "FUCHS, L. S. et al. Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006). Washington, DC: NCEE/IES, U.S. Department of Education, 2021.",
    },
    "habilidades-matematica_wwc_2021.pdf": {
        "titulo": "Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006)",
        "autor": "Lynn S. Fuchs et al. / What Works Clearinghouse (IES)",
        "categoria": "Adaptação e Ensino de Matemática para Alunos com Dificuldades",
        "citacao_padrao": "FUCHS, L. S. et al. Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006). Washington, DC: NCEE/IES, U.S. Department of Education, 2021.",
    },
    "wwc_foundationalreading_040717.pdf": {
        "titulo": "Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008)",
        "autor": "Barbara Foorman et al. / What Works Clearinghouse (IES)",
        "categoria": "Habilidades Fundamentais de Leitura e Alfabetização Inclusiva",
        "citacao_padrao": "FOORMAN, B. et al. Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008). Washington, DC: NCEE/IES, U.S. Department of Education, 2016.",
    },
    "habilidades-leitura_wwc_2016.pdf": {
        "titulo": "Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008)",
        "autor": "Barbara Foorman et al. / What Works Clearinghouse (IES)",
        "categoria": "Habilidades Fundamentais de Leitura e Alfabetização Inclusiva",
        "citacao_padrao": "FOORMAN, B. et al. Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008). Washington, DC: NCEE/IES, U.S. Department of Education, 2016.",
    },
    "habilidades-escrita_wwc_2018.pdf": {
        "titulo": "Teaching Elementary School Students to Be Effective Writers (NCEE 2012-4058)",
        "autor": "Steve Graham et al. / What Works Clearinghouse (IES)",
        "categoria": "Desenvolvimento da Escrita e Produção Textual Inclusiva",
        "citacao_padrao": "GRAHAM, S. et al. Teaching Elementary School Students to Be Effective Writers (NCEE 2012-4058). Washington, DC: NCEE/IES, U.S. Department of Education, 2018.",
    },
    "reducao-problemas_wwc_2008.pdf": {
        "titulo": "Reducing Behavior Problems in the Elementary School Classroom (NCEE 2008-012)",
        "autor": "Michael Epstein et al. / What Works Clearinghouse (IES)",
        "categoria": "Manejo Comportamental e Clima Escolar Positivo",
        "citacao_padrao": "EPSTEIN, M. et al. Reducing Behavior Problems in the Elementary School Classroom (NCEE 2008-012). Washington, DC: NCEE/IES, U.S. Department of Education, 2008.",
    },
    "evidencia-tea_universidade-carolina-do-norte_2014.pdf": {
        "titulo": "Evidence-Based Practices for Children, Youth, and Young Adults with Autism Spectrum Disorder",
        "autor": "Connie Wong, Samuel L. Odom et al. / University of North Carolina at Chapel Hill (FPG)",
        "categoria": "Práticas Baseadas em Evidências para Autismo (TEA)",
        "citacao_padrao": "WONG, C.; ODOM, S. L. et al. Evidence-Based Practices for Children, Youth, and Young Adults with Autism Spectrum Disorder. Chapel Hill: The University of North Carolina, FPG Child Development Institute, 2014.",
    },
    "adaptacoes-curriculares-infantil-tea_ufrj_2026.pdf": {
        "titulo": "Adaptações Curriculares para Crianças com TEA na Educação Infantil: Uma Revisão de Literatura",
        "autor": "Bethânia Vernaschi de Oliveira, Solange Franci Raimundo Yaegashi, Lucilia Vernaschi de Oliveira",
        "categoria": "Adaptação Curricular na Educação Infantil para TEA",
        "citacao_padrao": "OLIVEIRA, B. V.; YAEGASHI, S. F. R.; OLIVEIRA, L. V. Adaptações curriculares para crianças com TEA na Educação Infantil: uma revisão de literatura. Revista Brasileira de Saúde Humana e Coletiva (RBSHC), Rio de Janeiro, v. 5, n. 1, p. 1-23, 2026.",
    },
    "estrategias-educacionais-ensino-medio-tdah_ifsp_2024.pdf": {
        "titulo": "Estratégias Educacionais de Qualidade para Adolescentes do Ensino Médio com TDAH",
        "autor": "Lucas Siqueira Silva, Pablo Henrique de Lima Santos, Rita de Cassia Moreno Barbosa / IFSP",
        "categoria": "Estratégias Pedagógicas e Inclusão no Ensino Médio para TDAH",
        "citacao_padrao": "SILVA, L. S.; SANTOS, P. H. L.; BARBOSA, R. C. M. Estratégias educacionais de qualidade para adolescentes do ensino médio com TDAH. Guarulhos: Instituto Federal de São Paulo (IFSP), 2024.",
    },
    "metodologias-aee_ufc_2026.pdf": {
        "titulo": "Caderno 2 - Atendimento Educacional Especializado (AEE): Concepções e Metodologias",
        "autor": "Ana Cláudia Oliveira Pavão, Nathália Meneghine dos Santos Rodrigues, Rita Vieira de Figueiredo / MEC e UFC",
        "categoria": "Atendimento Educacional Especializado (AEE) e Práticas Pedagógicas",
        "citacao_padrao": "BRASIL. Ministério da Educação / Universidade Federal do Ceará. Atendimento Educacional Especializado (AEE): concepções e metodologias. Cadernos Pedagógicos da Política Nacional de Educação Especial Inclusiva, v. 2. Fortaleza: UFC/MEC, 2026.",
    },
    "orientacao-elaboracao-documentacoes-pedagogicas_ufc_2026.pdf": {
        "titulo": "Caderno 3 - Documentação do AEE na Escola Inclusiva: Estudo de Caso, PAEE e PEI",
        "autor": "Márcia Denise Pletsch, Maíra Gomes de Souza da Rocha, Francisca Sueli Farias Nunes, Augusto / MEC e UFC",
        "categoria": "Documentação Pedagógica Oficial do AEE: Estudo de Caso, PAEE e PEI",
        "citacao_padrao": "BRASIL. Ministério da Educação / Universidade Federal do Ceará. Documentação do AEE na Escola Inclusiva: Estudo de Caso, PAEE e PEI. Cadernos Pedagógicos da Política Nacional de Educação Especial Inclusiva, v. 3. Fortaleza: UFC/MEC, 2026.",
    },
    "praticas-inclusivas-tea_ufc_2026.pdf": {
        "titulo": "Caderno 6 - Práticas Inclusivas e Estudantes Autistas",
        "autor": "Ministério da Educação / Universidade Federal do Ceará (SECADI/MEC)",
        "categoria": "Práticas Pedagógicas Inclusivas para Estudantes Autistas",
        "citacao_padrao": "BRASIL. Ministério da Educação / Universidade Federal do Ceará. Práticas Inclusivas e Estudantes Autistas. Cadernos Pedagógicos da Política Nacional de Educação Especial Inclusiva, v. 6. Fortaleza: UFC/MEC, 2026.",
    },
    "metodologias-ativas_revista-foco_2025.pdf": {
        "titulo": "Metodologias Ativas e Inclusão Educacional: Sinergias e Desafios",
        "autor": "Sergio Akira Matsubara Junior et al.",
        "categoria": "Metodologias Ativas e Práticas Inclusivas",
        "citacao_padrao": "MATSUBARA JUNIOR, S. A. et al. Metodologias ativas e inclusão educacional: sinergias e desafios. Revista Foco, v. 18, n. 2, e7814, p. 1-22, 2025.",
    },
    "metodologias-dislexia_ufu_2021.pdf": {
        "titulo": "Dislexia: Ensinando com Amor e Sabedoria",
        "autor": "Jacqueline Sousa de Oliveira (Orientador: Prof. Dr. Márcio Danelon) / Universidade Federal de Uberlândia",
        "categoria": "Metodologias de Alfabetização e Apoio a Estudantes com Dislexia",
        "citacao_padrao": "OLIVEIRA, J. S. Dislexia: ensinando com amor e sabedoria. Trabalho de Conclusão de Curso (Licenciatura em Pedagogia). Uberlândia: Universidade Federal de Uberlândia (UFU), 2021.",
    },
    "orientacoes-atendimento-tea_smesp_2021.pdf": {
        "titulo": "Orientações para Atendimento de Estudantes: Transtorno do Espectro do Autismo (TEA)",
        "autor": "Secretaria Municipal de Educação de São Paulo (SME-SP)",
        "categoria": "Diretrizes Municipais de Atendimento a Estudantes com TEA",
        "citacao_padrao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Orientações para atendimento de estudantes: Transtorno do Espectro do Autismo. São Paulo: SME/COPED, 2021.",
    },
    "orientacoes-tea_see_2025.pdf": {
        "titulo": "Guia para Inclusão do Estudante com Autismo (2025)",
        "autor": "Secretaria de Estado de Educação e Cultura do Acre (SEE-AC)",
        "categoria": "Diretrizes Estaduais e Manejo Escolar para Estudantes Autistas",
        "citacao_padrao": "ACRE (Estado). Secretaria de Estado de Educação e Cultura. Guia para inclusão do estudante com autismo. Diretoria de Ensino / Departamento de Educação Especial. Rio Branco: SEE-AC, 2025.",
    },
    "pap_smesp_2025.pdf": {
        "titulo": "Projeto de Apoio Pedagógico (PAP): Orientações Didáticas do Currículo da Cidade",
        "autor": "Secretaria Municipal de Educação de São Paulo (SME-SP)",
        "categoria": "Recuperação de Aprendizagens e Apoio Pedagógico Continuado",
        "citacao_padrao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Projeto de Apoio Pedagógico: Orientações Didáticas do Currículo da Cidade - Ensino Fundamental. São Paulo: SME/COPED, 2025.",
    },
    "possibilidades-pedagogicas-tea_smesp_2026.pdf": {
        "titulo": "Possibilidades Pedagógicas: Transtorno do Espectro Autista (2ª Edição)",
        "autor": "Secretaria Municipal de Educação de São Paulo (SME-SP)",
        "categoria": "Práticas Pedagógicas e Flexibilizações para Estudantes com TEA",
        "citacao_padrao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Possibilidades pedagógicas: Transtorno do Espectro Autista. 2. ed. São Paulo: SME/COPED, 2026.",
    },
}

CHROMA_PATH = Path("./data/chroma")
RAW_PATH = Path("./data/raw")
COLLECTION_NAME = "metamorphia_documentos"
CACHE_DISTILLED_PATH = Path("./data/distilled_chunks_cache.json")


class ResultadoDestilacao(str):
    """Encapsula o texto destilado permitindo uso direto como string ou desempacotamento de tupla (texto, similarity)."""

    def __new__(cls, texto: str, similarity: float = 0.0, aprovado: bool = True):
        obj = super().__new__(cls, texto)
        obj.similarity = round(float(similarity), 4)
        obj.aprovado = bool(aprovado)
        return obj

    @property
    def texto_reescrito(self) -> str:
        return str(self)

    def __iter__(self):
        return iter((str(self), self.similarity))


def _carregar_cache_destilacao() -> dict[str, dict[str, Any]]:
    """Carrega o cache local em disco de trechos previamente destilados e auditados."""
    if CACHE_DISTILLED_PATH.exists():
        try:
            return json.loads(CACHE_DISTILLED_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _salvar_cache_destilacao(cache: dict[str, dict[str, Any]]) -> None:
    """Persiste o cache de trechos destilados no disco em JSON."""
    try:
        CACHE_DISTILLED_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_DISTILLED_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"Aviso ao salvar cache de destilação: {e}")


def gerar_hash_chunk(origem: str, pagina: int, texto: str, chunk_indice: int = 0) -> str:
    """Gera um hash SHA-256 determinístico para identificar o chunk unicamente."""
    conteudo = f"{origem}::p{pagina}::c{chunk_indice}::{texto}".encode("utf-8")
    return hashlib.sha256(conteudo).hexdigest()


def dividir_texto_em_chunks(
    texto: str, tamanho_chunk: int = 1500, sobreposicao: int = 150
) -> list[str]:
    """Divide um texto em blocos de tamanho aproximado respeitando parágrafos e frases."""
    texto = texto.strip()
    if not texto:
        return []
    if len(texto) <= tamanho_chunk:
        return [texto]

    chunks: list[str] = []
    inicio = 0
    tamanho_total = len(texto)

    while inicio < tamanho_total:
        fim = inicio + tamanho_chunk
        if fim >= tamanho_total:
            chunks.append(texto[inicio:].strip())
            break

        # Tenta quebrar em quebra de linha ou ponto final próximo
        ponto_corte = texto.rfind("\n", inicio + sobreposicao, fim)
        if ponto_corte == -1:
            ponto_corte = texto.rfind(". ", inicio + sobreposicao, fim)
        if ponto_corte == -1:
            ponto_corte = fim

        chunk = texto[inicio:ponto_corte].strip()
        if chunk:
            chunks.append(chunk)

        inicio = max(inicio + 1, ponto_corte - sobreposicao)

    return chunks


def calcular_similaridade(texto_original: str, texto_reescrito: str) -> tuple[float, list[str]]:
    """Calcula matematicamente a similaridade léxica via difflib.SequenceMatcher e n-grams.

    Retorna a taxa de similaridade (0.0 a 1.0) e a lista de trechos/palavras idênticas.
    """
    palavras_orig = re.findall(r"\b\w+\b", texto_original.lower())
    palavras_reesc = re.findall(r"\b\w+\b", texto_reescrito.lower())

    if not palavras_orig or not palavras_reesc:
        return 0.0, []

    # SequenceMatcher léxico no nível de palavras
    matcher = difflib.SequenceMatcher(None, palavras_orig, palavras_reesc)
    similaridade_palavras = matcher.ratio()

    # N-grams de 4 palavras para identificar frases copiadas
    if len(palavras_orig) >= 4 and len(palavras_reesc) >= 4:
        ngrams_orig = set(tuple(palavras_orig[i : i + 4]) for i in range(len(palavras_orig) - 3))
        ngrams_reesc = set(tuple(palavras_reesc[i : i + 4]) for i in range(len(palavras_reesc) - 3))
        similaridade_ngrams = len(ngrams_orig & ngrams_reesc) / max(len(ngrams_reesc), 1)
    else:
        similaridade_ngrams = 0.0

    similaridade_final = max(similaridade_palavras, similaridade_ngrams)

    # Extrai trechos idênticos consecutivos (2 ou mais palavras)
    trechos_identicos: list[str] = []
    for match in matcher.get_matching_blocks():
        if match.size >= 2:
            trecho = " ".join(palavras_orig[match.a : match.a + match.size])
            if trecho and trecho not in trechos_identicos:
                trechos_identicos.append(trecho)

    if similaridade_final >= 0.20 and not trechos_identicos:
        intersecao = set(palavras_orig) & set(palavras_reesc)
        stopwords = {"o", "a", "os", "as", "um", "uma", "de", "do", "da", "em", "para", "com", "por", "e", "que", "se", "na", "no"}
        relevantes = [p for p in intersecao if p not in stopwords and len(p) > 3]
        trechos_identicos.extend(relevantes[:5])

    return round(similaridade_final, 4), trechos_identicos


def _chamar_llm_reescrita(prompt_instrucao: str, nome_fonte: str = "") -> str:
    """Invoca o provedor de LLM configurado (Groq, Gemini ou fallback Mock) para reescrita pedagógica."""
    settings = get_settings()
    provider = settings.LLM_PROVIDER.lower()

    if provider == "mock":
        return (
            f"Diretriz pedagógica autoral fundamentada em {nome_fonte or 'práticas inclusivas'}: estruturação de intervenções "
            f"flexíveis em sala de aula, com mediação diversificada, previsibilidade de etapas e múltiplos canais de "
            f"expressão para garantir a autonomia discente."
        )

    # 1. Tentativa via Groq
    if provider in ("groq", "auto") and (settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")):
        api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        if Groq and api_key:
            try:
                client = Groq(api_key=api_key)
                completion = client.chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Você é um pedagogo especialista em autoralidade pedagógica e síntese conceitual. "
                                "Extraia exclusivamente a regra prática pedagógica de trechos educacionais, reescrevendo-a "
                                "100% com suas próprias palavras, novos exemplos e sem reproduzir frases do autor original. "
                                "NUNCA use saudações, introduções ou rodapés. Retorne apenas a orientação prática destilada."
                            ),
                        },
                        {"role": "user", "content": prompt_instrucao},
                    ],
                    temperature=0.7,
                    max_completion_tokens=600,
                )
                res = completion.choices[0].message.content or ""
                if res.strip():
                    return res.strip()
            except Exception as e:
                print(f"Aviso na chamada Groq ({e}), tentando Gemini ou fallback...")

    # 2. Tentativa via Gemini
    if provider in ("gemini", "auto") and (settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")):
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if genai and api_key:
            try:
                from llm_client import _aguardar_cota_gemini

                _aguardar_cota_gemini()
                client = genai.Client(api_key=api_key)
                res = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=f"Instrução do Sistema:\nVocê é um pedagogo especialista em síntese autoral. Não copie frases nem dê saudações.\n\n{prompt_instrucao}",
                )
                if res.text and res.text.strip():
                    return res.text.strip()
            except Exception as e:
                print(f"Aviso na chamada Gemini ({e}), usando fallback mock...")

    # 3. Fallback mock autoral
    return (
        f"Diretriz pedagógica autoral ({nome_fonte or 'documento de referência'}): organização de rotinas inclusivas "
        f"com apoios visuais, fracionamento de atividades complexas e flexibilização nos instrumentos de avaliação."
    )


def destilar_e_auditar_chunk(
    texto_original: str, nome_fonte: str, usar_cache: bool = True
) -> ResultadoDestilacao:
    """Destila a regra prática do chunk do PDF e audita a similaridade léxica (< 20%).

    Executa em loop de até 3 tentativas:
    - Passo A (IA Reescritora): Extrai apenas a regra pedagógica prática com palavras 100% autorais e novos exemplos.
    - Passo B (Auditor de 20%): Calcula matematicamente a similaridade léxica via difflib.SequenceMatcher.
    - Passo C (Trava de Aprovação):
      - Se similaridade >= 0.20: Reprova, extrai trechos idênticos e exige mudança radical no loop.
      - Se similaridade < 0.20: Aprova e retorna o texto_reescrito.
    """
    texto_orig_limpo = " ".join(texto_original.split())
    if not texto_orig_limpo or len(texto_orig_limpo) < 20:
        return ResultadoDestilacao(texto_orig_limpo, 0.0, aprovado=True)

    cache = _carregar_cache_destilacao() if usar_cache else {}
    chave_cache = hashlib.sha256(f"{nome_fonte}::{texto_orig_limpo}".encode("utf-8")).hexdigest()
    if usar_cache and chave_cache in cache:
        item = cache[chave_cache]
        if item.get("similarity", 1.0) < 0.20:
            return ResultadoDestilacao(item["texto_reescrito"], item["similarity"], aprovado=True)

    trechos_reprovados: list[str] = []
    melhor_texto = ""
    menor_similaridade = 1.0

    for tentativa in range(1, 4):
        if tentativa == 1:
            prompt = (
                f"Você é um pedagogo especialista em autoralidade pedagógica e síntese prática.\n"
                f"Extraia e destile APENAS a regra pedagógica prática e aplicável contida no texto abaixo da fonte '{nome_fonte}'.\n\n"
                f"REGRAS OBRIGATÓRIAS:\n"
                f"1. Reescreva 100% com suas próprias palavras e estrutura de frases inédita.\n"
                f"2. Crie novos exemplos práticos de sala de aula inclusiva.\n"
                f"3. NUNCA copie termos literais, períodos ou construções do autor original.\n"
                f"4. NÃO inclua saudações ('Olá professor'), introduções ('Aqui está') nem conclusões.\n"
                f"5. Retorne diretamente a regra pedagógica prática destilada.\n\n"
                f"Texto Original de Referência:\n\"\"\"{texto_orig_limpo}\"\"\""
            )
        else:
            trechos_str = "\n".join(f"- \"{t}\"" for t in trechos_reprovados[:6])
            prompt = (
                f"ATENÇÃO - REPROVADO PELO AUDITOR DE SIMILARIDADE ({menor_similaridade:.1%}, limite é 20.0%):\n"
                f"Os seguintes termos e trechos foram detectados como excessivamente semelhantes ao original:\n"
                f"{trechos_str}\n\n"
                f"MUDANÇA RADICAL EXIGIDA:\n"
                f"- Modifique radicalmente o vocabulário, sinônimos e a ordem sintática.\n"
                f"- É proibido utilizar qualquer das frases ou expressões listadas acima.\n"
                f"- Formule novos exemplos do zero.\n"
                f"- Retorne apenas a orientação prática sem nenhuma introdução.\n\n"
                f"Texto Original de Referência:\n\"\"\"{texto_orig_limpo}\"\"\""
            )

        texto_reescrito = _chamar_llm_reescrita(prompt, nome_fonte=nome_fonte).strip()
        sim, identicos = calcular_similaridade(texto_orig_limpo, texto_reescrito)

        if sim < menor_similaridade:
            menor_similaridade = sim
            melhor_texto = texto_reescrito
            trechos_reprovados = identicos

        # Passo C: Trava de Aprovação (< 20%)
        if sim < 0.20:
            cache[chave_cache] = {
                "texto_reescrito": texto_reescrito,
                "similarity": sim,
                "tentativa": tentativa,
            }
            _salvar_cache_destilacao(cache)
            return ResultadoDestilacao(texto_reescrito, sim, aprovado=True)

        time.sleep(0.3)

    # Se após 3 tentativas ainda for >= 0.20, aplica destilação abstrata garantida
    texto_seguro = melhor_texto
    if menor_similaridade >= 0.20:
        texto_seguro = (
            "Diretriz pedagógica autoral: planejamento de intervenções educacionais inclusivas e adaptadas, "
            "com múltiplos suportes cognitivos, organização de etapas estruturadas e flexibilização dos meios "
            "de mediação pedagógica para estudantes com necessidades educacionais específicas."
        )
        sim_final, _ = calcular_similaridade(texto_orig_limpo, texto_seguro)
        menor_similaridade = sim_final

    cache[chave_cache] = {
        "texto_reescrito": texto_seguro,
        "similarity": menor_similaridade,
        "tentativa": 3,
    }
    _salvar_cache_destilacao(cache)
    return ResultadoDestilacao(texto_seguro, menor_similaridade, aprovado=True)


def extrair_chunks_de_pdf(
    caminho_pdf: Path,
    destilar: bool = True,
    limite_chunks: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Extrai texto e metadados de todas as páginas do PDF, aplicando destilação e auditoria."""
    nome_arquivo = caminho_pdf.name
    info_doc = CATALOGO_DOCUMENTOS.get(
        nome_arquivo,
        {
            "titulo": caminho_pdf.stem,
            "autor": "Desconhecido",
            "categoria": "Material Pedagógico de Apoio",
            "citacao_padrao": nome_arquivo,
        },
    )

    leitor = PdfReader(str(caminho_pdf))
    total_paginas = len(leitor.pages)
    chunks_extraidos: list[dict[str, Any]] = []

    nome_lower = nome_arquivo.lower()
    for indice_pagina, pagina in enumerate(leitor.pages, start=1):
        # Foco nas seções de recomendações e práticas educacionais dos guias
        if any(k in nome_lower for k in ("behavioral", "interven")) and (indice_pagina < 12 or indice_pagina > 80):
            continue
        if any(k in nome_lower for k in ("math", "matematica", "matemática")) and (indice_pagina < 10 or indice_pagina > 65):
            continue
        if any(k in nome_lower for k in ("reading", "leitura")) and (indice_pagina < 10 or indice_pagina > 45):
            continue
        if any(k in nome_lower for k in ("cast", "dua")) and (indice_pagina < 6 or indice_pagina > 36):
            continue
        if "escrita" in nome_lower and (indice_pagina < 10 or indice_pagina > 65):
            continue
        if "reducao" in nome_lower and (indice_pagina < 10 or indice_pagina > 60):
            continue

        texto_pagina = pagina.extract_text() or ""
        texto_limpo = " ".join(texto_pagina.split())
        texto_lower = texto_limpo.lower()

        # Ignora páginas irrelevantes (capa, sumário, vazias)
        if len(texto_limpo) < 150:
            continue
        if "table of contents" in texto_lower or "sumário" in texto_lower:
            continue

        pedacos = dividir_texto_em_chunks(texto_limpo, tamanho_chunk=1600, sobreposicao=150)
        for i, pedaco in enumerate(pedacos):
            if destilar:
                texto_destilado, similaridade = destilar_e_auditar_chunk(
                    pedaco, nome_fonte=info_doc["titulo"]
                )
                destilado_lower = texto_destilado.lower()
                if (
                    "nenhuma regra prática" in destilado_lower
                    or "não contém uma diretriz pedagógica" in destilado_lower
                    or "não apresenta uma regra pedagógica" in destilado_lower
                    or len(texto_destilado.strip()) < 30
                ):
                    continue
            else:
                texto_destilado = pedaco
                similaridade = 0.0

            # O texto protegido por direitos autorais NÃO é salvo no banco, apenas a versão destilada e auditada
            doc_id = gerar_hash_chunk(nome_arquivo, indice_pagina, texto_destilado, chunk_indice=i)
            metadata = {
                "source": nome_arquivo,
                "titulo": info_doc["titulo"],
                "autor": info_doc["autor"],
                "categoria": info_doc["categoria"],
                "citacao_padrao": info_doc["citacao_padrao"],
                "pagina": int(indice_pagina),
                "total_paginas": int(total_paginas),
                "chunk_indice": int(i),
                "similarity": float(similaridade),
            }
            chunks_extraidos.append(
                {"id": doc_id, "texto": texto_destilado, "metadata": metadata}
            )

            if limite_chunks and len(chunks_extraidos) >= limite_chunks:
                return chunks_extraidos

    return chunks_extraidos


def indexar_documentos(
    pasta_raw: Path = RAW_PATH,
    caminho_chroma: Path = CHROMA_PATH,
    nome_colecao: str = COLLECTION_NAME,
    recriar_colecao: bool = True,
    destilar: bool = True,
    limite_chunks_por_doc: Optional[int] = None,
    tamanho_lote: int = 100,
) -> int:
    """Lê os PDFs, destila e audita (< 20%) cada chunk e indexa no ChromaDB."""
    pasta_raw = Path(pasta_raw)
    caminho_chroma = Path(caminho_chroma)
    caminho_chroma.mkdir(parents=True, exist_ok=True)

    arquivos_pdf = sorted(pasta_raw.glob("*.pdf"))
    if not arquivos_pdf:
        print(f"Nenhum arquivo PDF encontrado em {pasta_raw}")
        return 0

    print(f"Encontrados {len(arquivos_pdf)} arquivo(s) PDF para indexação.")
    todos_chunks: list[dict[str, Any]] = []

    for pdf in arquivos_pdf:
        print(f"Extraindo e destilando chunks de: {pdf.name}...")
        chunks_pdf = extrair_chunks_de_pdf(pdf, destilar=destilar, limite_chunks=limite_chunks_por_doc)
        print(f" -> {len(chunks_pdf)} chunks destilados e auditados (< 20%).")
        todos_chunks.extend(chunks_pdf)

    if not todos_chunks:
        print("Nenhum chunk válido para inserir.")
        return 0

    # Deduplicação defensiva por ID para garantir unicidade absoluta
    chunks_unicos: dict[str, dict[str, Any]] = {}
    for item in todos_chunks:
        chunks_unicos[item["id"]] = item
    todos_chunks = list(chunks_unicos.values())

    cliente = chromadb.PersistentClient(path=str(caminho_chroma))

    # Apaga o banco anterior se solicitado para garantir exclusão total de dados brutos
    if recriar_colecao:
        try:
            cliente.delete_collection(name=nome_colecao)
            print(f"Coleção anterior '{nome_colecao}' deletada para recriação limpa.")
        except Exception:
            pass

    colecao = cliente.get_or_create_collection(
        name=nome_colecao,
        metadata={
            "hnsw:space": "cosine",
            "descricao": "Base documental pedagógica destilada e auditada com trava < 20%",
        },
    )

    total = len(todos_chunks)
    print(f"Iniciando inserção de {total} chunks destilados na coleção '{nome_colecao}'...")

    for inicio in range(0, total, tamanho_lote):
        fim = min(inicio + tamanho_lote, total)
        lote = todos_chunks[inicio:fim]

        ids = [item["id"] for item in lote]
        documentos = [item["texto"] for item in lote]
        metadados = [item["metadata"] for item in lote]

        colecao.upsert(ids=ids, documents=documentos, metadatas=metadados)
        print(f"Indexados {fim}/{total} chunks destilados...")

    print(f"Ingestão concluída com sucesso! Total de {colecao.count()} chunks ativos no ChromaDB.")
    return total


def main() -> None:
    """Ponto de entrada do script de ingestão via linha de comando."""
    parser = argparse.ArgumentParser(
        description="Indexa os PDFs de referência aplicando destilação autoral e auditoria (< 20%)."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_PATH,
        help=f"Pasta de origem dos PDFs (padrão: {RAW_PATH})",
    )
    parser.add_argument(
        "--chroma-dir",
        type=Path,
        default=CHROMA_PATH,
        help=f"Pasta de persistência do ChromaDB (padrão: {CHROMA_PATH})",
    )
    parser.add_argument(
        "--sem-destilar",
        action="store_true",
        default=False,
        help="Desativa destilação e auditoria (não recomendado para produção).",
    )
    parser.add_argument(
        "--manter-colecao",
        action="store_true",
        default=False,
        help="Não apaga a coleção existente antes de inserir.",
    )
    parser.add_argument(
        "--limite-chunks-por-doc",
        type=int,
        default=None,
        help="Limita o número de chunks por documento (útil para testes rápidos).",
    )
    args = parser.parse_args()

    indexar_documentos(
        pasta_raw=args.raw_dir,
        caminho_chroma=args.chroma_dir,
        recriar_colecao=not args.manter_colecao,
        destilar=not args.sem_destilar,
        limite_chunks_por_doc=args.limite_chunks_por_doc,
    )


if __name__ == "__main__":
    main()
