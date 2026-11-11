from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from pypdf import PdfReader

PASTA_RAW = Path(__file__).resolve().parent / "data" / "raw"

ANALISE_JURIDICA_DOCUMENTOS: dict[str, dict[str, Any]] = {
    "D12686.pdf": {
        "titulo": "Decreto Federal nº 12.686, de 20 de outubro de 2025",
        "tipo_obra": "Norma Jurídica Oficial / Ato Governamental Federal",
        "titular": "República Federativa do Brasil / Presidência da República / MEC",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/1998 (Lei de Direitos Autorais), Art. 8º, inciso IV: 'Não são objeto de proteção como direitos autorais de que trata esta Lei: [...] os textos de tratados ou convenções, leis, decretos, regulamentos, decisões judiciais e demais atos oficiais.'",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Nenhuma restrição patrimonial. O texto legal é de livre acesso e reprodução irrestrita no território brasileiro.",
        "recomendacao_citacao": "BRASIL. Decreto nº 12.686, de 20 de outubro de 2025. Diário Oficial da União.",
    },
    "decreto-12686_pneei_2025.pdf": {
        "titulo": "Decreto Federal nº 12.686/2025 - Política Nacional de Educação Especial Inclusiva (PNEEI)",
        "tipo_obra": "Norma Jurídica Oficial / Ato Governamental Federal",
        "titular": "República Federativa do Brasil / Câmara dos Deputados",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/1998, Art. 8º, IV (Atos e textos oficiais não são objeto de proteção de direitos autorais).",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Nenhuma restrição patrimonial.",
        "recomendacao_citacao": "BRASIL. Decreto nº 12.686, de 20 de outubro de 2025. Diário Oficial da União / Câmara dos Deputados.",
    },
    "behavioral-interventions-practice-guide_v3a_508a.pdf": {
        "titulo": "Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "Institute of Education Sciences (IES) / U.S. Department of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (U.S. Copyright Act): 'Copyright protection under this title is not available for any work of the United States Government.' Obras oficiais do governo federal norte-americano são de Domínio Público nos EUA. Para uso internacional e no Brasil, aplica-se o Art. 46, III da Lei 9.610/98 (direito de citação e consulta científica) e a doutrina de Fair Use (17 U.S.C. § 107).",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Não alegar endosso oficial por parte do Departamento de Educação dos EUA. A citação da fonte oficial deve ser mantida.",
        "recomendacao_citacao": "U.S. DEPARTMENT OF EDUCATION. WWC Practice Guide: Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001). IES, 2024.",
    },
    "intervenções_wwc_2024.pdf": {
        "titulo": "Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "Institute of Education Sciences (IES) / U.S. Department of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Manter a atribuição da fonte governamental oficial.",
        "recomendacao_citacao": "U.S. DEPARTMENT OF EDUCATION. WWC Practice Guide: Teacher-Delivered Behavioral Interventions in Grades K–5 (WWC 2025001). IES, 2024.",
    },
    "WWC2021006-Math-PG.pdf": {
        "titulo": "Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "National Center for Education Evaluation and Regional Assistance (NCEE) / IES / U.S. Dept of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III (Citação e consulta para fins pedagógicos/científicos).",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar os autores principais (Fuchs et al.) e o What Works Clearinghouse. Não vender o documento bruto de forma isolada sem valor agregado.",
        "recomendacao_citacao": "FUCHS, L. S. et al. Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006). IES, 2021.",
    },
    "habilidades-matematica_wwc_2021.pdf": {
        "titulo": "Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "NCEE / IES / U.S. Dept of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar os autores principais e o IES/WWC.",
        "recomendacao_citacao": "FUCHS, L. S. et al. Assisting Students Struggling with Mathematics: Intervention in the Elementary Grades (WWC 2021006). IES, 2021.",
    },
    "wwc_foundationalreading_040717.pdf": {
        "titulo": "Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "NCEE / Institute of Education Sciences / U.S. Department of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III (Citação de obras científicas e educativas).",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar os autores e a agência federal publicadora. O uso como base de conhecimento consultiva com referência é perfeitamente lícito.",
        "recomendacao_citacao": "FOORMAN, B. et al. Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade. IES, 2016.",
    },
    "habilidades-leitura_wwc_2016.pdf": {
        "titulo": "Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade (NCEE 2016-4008)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "NCEE / IES / U.S. Dept of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Manter a atribuição dos autores e da agência pública.",
        "recomendacao_citacao": "FOORMAN, B. et al. Foundational Skills to Support Reading for Understanding in Kindergarten Through 3rd Grade. IES, 2016.",
    },
    "habilidades-escrita_wwc_2018.pdf": {
        "titulo": "Teaching Elementary School Students to Be Effective Writers (NCEE 2012-4058)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "NCEE / IES / U.S. Dept of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar autores principais (Graham et al.) e o WWC.",
        "recomendacao_citacao": "GRAHAM, S. et al. Teaching Elementary School Students to Be Effective Writers (NCEE 2012-4058). IES, 2018.",
    },
    "reducao-problemas_wwc_2008.pdf": {
        "titulo": "Reducing Behavior Problems in the Elementary School Classroom (NCEE 2008-012)",
        "tipo_obra": "Guia de Prática Educacional do Governo Federal dos EUA",
        "titular": "NCEE / IES / U.S. Dept of Education",
        "jurisdicao": "Estados Unidos / Internacional",
        "enquadramento_legal": "17 U.S.C. § 105 (Public Domain) e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar autores principais (Epstein et al.) e o WWC.",
        "recomendacao_citacao": "EPSTEIN, M. et al. Reducing Behavior Problems in the Elementary School Classroom (NCEE 2008-012). IES, 2008.",
    },
    "cast-diretrizes-do-desenho-universal-para-a-aprendizagem.pdf": {
        "titulo": "Diretrizes do Desenho Universal para a Aprendizagem (DUA) versão 3.0",
        "tipo_obra": "Estrutura Pedagógica Aberta / Tradução Acadêmica Autorizada",
        "titular": "CAST (Center for Applied Special Technology, 2024) / Tradução: Prof. Dr. Eladio Sebastián-Heredero",
        "jurisdicao": "Global / Brasil",
        "enquadramento_legal": "As Diretrizes DUA da CAST são distribuídas sob licença Creative Commons Atribuição-CompartilhaIgual (CC BY-SA 4.0) e Recursos Educacionais Abertos (REA). No Brasil, o Art. 46, inciso III da Lei 9.610/98 autoriza a citação e menção de passagens. O uso consultivo por IA para fundamentar adaptações pedagógicas é plenamente válido.",
        "status_comercial": "Permitido com Atribuição Obrigatória",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Obrigatório manter o crédito à CAST e ao tradutor Prof. Dr. Eladio Sebastián-Heredero. Não alegar autoria própria da metodologia DUA.",
        "recomendacao_citacao": "CAST (2024). Diretrizes do Desenho Universal para a Aprendizagem versão 3.0. Wakefield, MA. Tradução: Eladio Sebastián-Heredero.",
    },
    "dua-diretrizes_cast_2024.pdf": {
        "titulo": "Diretrizes do Desenho Universal para a Aprendizagem (DUA) versão 3.0",
        "tipo_obra": "Estrutura Pedagógica Aberta / Recursos Educacionais Abertos (CC BY-SA 4.0)",
        "titular": "CAST (2024) / Tradução: Prof. Dr. Eladio Sebastián-Heredero",
        "jurisdicao": "Global / Brasil",
        "enquadramento_legal": "Licença Creative Commons CC BY-SA 4.0 e Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Obrigatória",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Manter crédito formal à CAST e ao tradutor.",
        "recomendacao_citacao": "CAST (2024). Diretrizes do Desenho Universal para a Aprendizagem versão 3.0. Tradução: Eladio Sebastián-Heredero.",
    },
    "evidencia-tea_universidade-carolina-do-norte_2014.pdf": {
        "titulo": "Evidence-Based Practices for Children, Youth, and Young Adults with Autism Spectrum Disorder",
        "tipo_obra": "Relatório de Pesquisa Acadêmica Aberta",
        "titular": "Frank Porter Graham Child Development Institute / University of North Carolina at Chapel Hill",
        "jurisdicao": "Estados Unidos / Global",
        "enquadramento_legal": "Fair Use (17 U.S.C. § 107) e Lei nº 9.610/98, Art. 46, III (Citação científica para fins educacionais).",
        "status_comercial": "Permitido com Atribuição Formal",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Atribuir autoria a Wong, Odom et al. e FPG Child Development Institute.",
        "recomendacao_citacao": "WONG, C. et al. Evidence-Based Practices for Children, Youth, and Young Adults with Autism Spectrum Disorder. Chapel Hill: UNC/FPG, 2014.",
    },
    "adaptacoes-curriculares-infantil-tea_ufrj_2026.pdf": {
        "titulo": "Adaptações Curriculares para Crianças com TEA na Educação Infantil: Uma Revisão de Literatura",
        "tipo_obra": "Artigo Científico em Periódico de Acesso Aberto (RBSHC)",
        "titular": "Bethânia Vernaschi de Oliveira, Solange F. R. Yaegashi, Lucilia V. de Oliveira / UFRJ / RBSHC",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/1998, Art. 46, inciso III (Direito de citação de estudos científicos para estudo e crítica com menção ao autor).",
        "status_comercial": "Permitido com Atribuição Científica",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar os autores e o periódico científico.",
        "recomendacao_citacao": "OLIVEIRA, B. V.; YAEGASHI, S. F. R.; OLIVEIRA, L. V. Adaptações curriculares para crianças com TEA na Educação Infantil. RBSHC, v. 5, n. 1, p. 1-23, 2026.",
    },
    "estrategias-educacionais-ensino-medio-tdah_ifsp_2024.pdf": {
        "titulo": "Estratégias Educacionais de Qualidade para Adolescentes do Ensino Médio com TDAH",
        "tipo_obra": "Relatório de Pesquisa Aplicada Institucional",
        "titular": "Instituto Federal de Educação, Ciência e Tecnologia de São Paulo (IFSP Campus Guarulhos)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 46, III (Citação e consulta para fins didático-pedagógicos).",
        "status_comercial": "Permitido com Atribuição Institucional",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar autores e o IFSP.",
        "recomendacao_citacao": "SILVA, L. S.; SANTOS, P. H. L.; BARBOSA, R. C. M. Estratégias educacionais de qualidade para adolescentes do ensino médio com TDAH. IFSP, 2024.",
    },
    "metodologias-aee_ufc_2026.pdf": {
        "titulo": "Caderno 2 - Atendimento Educacional Especializado (AEE): Concepções e Metodologias",
        "tipo_obra": "Publicação Oficial Governamental / Caderno Pedagógico",
        "titular": "Ministério da Educação (SECADI/MEC) / Universidade Federal do Ceará (UFC)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV (Atos e publicações oficiais da administração pública federal) e Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar o Ministério da Educação e a Universidade Federal do Ceará.",
        "recomendacao_citacao": "BRASIL. Ministério da Educação / UFC. Atendimento Educacional Especializado (AEE): concepções e metodologias. Fortaleza: UFC/MEC, 2026.",
    },
    "orientacao-elaboracao-documentacoes-pedagogicas_ufc_2026.pdf": {
        "titulo": "Caderno 3 - Documentação do AEE na Escola Inclusiva: Estudo de Caso, PAEE e PEI",
        "tipo_obra": "Publicação Oficial Governamental / Caderno Pedagógico",
        "titular": "Ministério da Educação (SECADI/MEC) / Universidade Federal do Ceará (UFC)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV e Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar o Ministério da Educação e autoras da elaboração técnica.",
        "recomendacao_citacao": "BRASIL. Ministério da Educação / UFC. Documentação do AEE na Escola Inclusiva: Estudo de Caso, PAEE e PEI. Fortaleza: UFC/MEC, 2026.",
    },
    "praticas-inclusivas-tea_ufc_2026.pdf": {
        "titulo": "Caderno 6 - Práticas Inclusivas e Estudantes Autistas",
        "tipo_obra": "Publicação Oficial Governamental / Caderno Pedagógico",
        "titular": "Ministério da Educação (SECADI/MEC) / Universidade Federal do Ceará (UFC)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV e Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Oficial",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar MEC / SECADI e UFC.",
        "recomendacao_citacao": "BRASIL. Ministério da Educação / UFC. Práticas Inclusivas e Estudantes Autistas. Fortaleza: UFC/MEC, 2026.",
    },
    "metodologias-ativas_revista-foco_2025.pdf": {
        "titulo": "Metodologias Ativas e Inclusão Educacional: Sinergias e Desafios",
        "tipo_obra": "Artigo Científico em Periódico Open Access",
        "titular": "Sergio Akira Matsubara Junior et al. / Revista Foco",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 46, III (Citação de artigos para fins pedagógicos e de pesquisa).",
        "status_comercial": "Permitido com Atribuição Científica",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Manter a referência bibliográfica aos autores e à Revista Foco.",
        "recomendacao_citacao": "MATSUBARA JUNIOR, S. A. et al. Metodologias ativas e inclusão educacional: sinergias e desafios. Revista Foco, v. 18, n. 2, e7814, 2025.",
    },
    "metodologias-dislexia_ufu_2021.pdf": {
        "titulo": "Dislexia: Ensinando com Amor e Sabedoria",
        "tipo_obra": "Trabalho de Conclusão de Curso / Produção Acadêmica Aberta",
        "titular": "Jacqueline Sousa de Oliveira / Universidade Federal de Uberlândia (UFU)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 46, III.",
        "status_comercial": "Permitido com Atribuição Acadêmica",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar a autora e a Universidade Federal de Uberlândia.",
        "recomendacao_citacao": "OLIVEIRA, J. S. Dislexia: ensinando com amor e sabedoria. TCC (Pedagogia). Uberlândia: UFU, 2021.",
    },
    "orientacoes-atendimento-tea_smesp_2021.pdf": {
        "titulo": "Orientações para Atendimento de Estudantes: Transtorno do Espectro do Autismo (TEA)",
        "tipo_obra": "Documento Oficial Municipal / Diretriz Pedagógica da Cidade",
        "titular": "Prefeitura da Cidade de São Paulo / Secretaria Municipal de Educação (SME-SP)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV (Textos e orientações oficiais emitidas por órgãos públicos).",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar a Secretaria Municipal de Educação de São Paulo.",
        "recomendacao_citacao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Orientações para atendimento de estudantes: TEA. São Paulo: SME/COPED, 2021.",
    },
    "orientacoes-tea_see_2025.pdf": {
        "titulo": "Guia para Inclusão do Estudante com Autismo (2025)",
        "tipo_obra": "Documento Oficial Estadual / Guia Pedagógico de Inclusão",
        "titular": "Governo do Estado do Acre / Secretaria de Estado de Educação e Cultura (SEE-AC)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV e Art. 46, III.",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar a Secretaria de Estado de Educação e Cultura do Acre.",
        "recomendacao_citacao": "ACRE (Estado). Secretaria de Estado de Educação e Cultura. Guia para inclusão do estudante com autismo. Rio Branco: SEE-AC, 2025.",
    },
    "pap_smesp_2025.pdf": {
        "titulo": "Projeto de Apoio Pedagógico (PAP): Orientações Didáticas do Currículo da Cidade",
        "tipo_obra": "Documento Oficial Municipal / Orientações Curriculares da Cidade",
        "titular": "Prefeitura da Cidade de São Paulo / Secretaria Municipal de Educação (SME-SP)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV.",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar a Secretaria Municipal de Educação de São Paulo.",
        "recomendacao_citacao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Projeto de Apoio Pedagógico: Orientações Didáticas do Currículo da Cidade. São Paulo: SME/COPED, 2025.",
    },
    "possibilidades-pedagogicas-tea_smesp_2026.pdf": {
        "titulo": "Possibilidades Pedagógicas: Transtorno do Espectro Autista (2ª Edição)",
        "tipo_obra": "Documento Oficial Municipal / Caderno de Práticas Pedagógicas",
        "titular": "Prefeitura da Cidade de São Paulo / Secretaria Municipal de Educação (SME-SP)",
        "jurisdicao": "Brasil",
        "enquadramento_legal": "Lei nº 9.610/98, Art. 8º, IV.",
        "status_comercial": "Totalmente Permitido (Domínio Público)",
        "pode_usar_em_ia_comercial": True,
        "requer_atribuicao": True,
        "restricoes": "Citar a Secretaria Municipal de Educação de São Paulo.",
        "recomendacao_citacao": "SÃO PAULO (Município). Secretaria Municipal de Educação. Possibilidades pedagógicas: Transtorno do Espectro Autista. 2. ed. São Paulo: SME/COPED, 2026.",
    },
}


def verificar_arquivo_pdf(caminho_arquivo: Path) -> dict[str, Any]:
    """Verifica um arquivo PDF individual e retorna dados técnicos e parecer jurídico."""
    nome = caminho_arquivo.name
    try:
        leitor = PdfReader(str(caminho_arquivo))
        total_paginas = len(leitor.pages)
        metadados = leitor.metadata or {}
        titulo_meta = getattr(metadados, "title", None) or nome
        autor_meta = getattr(metadados, "author", None) or "Não informado nos metadados do PDF"
    except Exception as erro:
        return {
            "arquivo": nome,
            "status_leitura": "erro",
            "detalhes_erro": str(erro),
            "pode_usar_em_ia_comercial": False,
        }

    parecer_padrao = {
        "titulo": titulo_meta,
        "tipo_obra": "Documento Pedagógico Genérico",
        "titular": autor_meta,
        "jurisdicao": "Não identificada",
        "enquadramento_legal": "Sujeito a verificação individual nos termos da Lei nº 9.610/98.",
        "status_comercial": "Requer Análise Adicional",
        "pode_usar_em_ia_comercial": False,
        "requer_atribuicao": True,
        "restricoes": "Verificar se há termos explícitos de proibição de processamento computacional.",
        "recomendacao_citacao": nome,
    }

    analise = ANALISE_JURIDICA_DOCUMENTOS.get(nome, parecer_padrao)

    return {
        "arquivo": nome,
        "caminho": str(caminho_arquivo.resolve()),
        "paginas": total_paginas,
        "titulo": analise["titulo"],
        "titular": analise["titular"],
        "tipo_obra": analise["tipo_obra"],
        "jurisdicao": analise["jurisdicao"],
        "enquadramento_legal": analise["enquadramento_legal"],
        "status_comercial": analise["status_comercial"],
        "pode_usar_em_ia_comercial": analise["pode_usar_em_ia_comercial"],
        "requer_atribuicao": analise["requer_atribuicao"],
        "restricoes": analise["restricoes"],
        "recomendacao_citacao": analise["recomendacao_citacao"],
    }


def verificar_todos_documentos(pasta_raw: Path = PASTA_RAW) -> list[dict[str, Any]]:
    """Percorre todos os PDFs da pasta raw e gera a lista com a auditoria de cada um."""
    if not pasta_raw.exists():
        return []
    arquivos = sorted(pasta_raw.glob("*.pdf"))
    return [verificar_arquivo_pdf(arq) for arq in arquivos]


def gerar_relatorio_conformidade(pasta_raw: Path = PASTA_RAW) -> str:
    """Gera um relatório formatado em texto para visualização no terminal ou log."""
    resultados = verificar_todos_documentos(pasta_raw)
    linhas = [
        "=" * 80,
        "RELATÓRIO DE CONFORMIDADE JURÍDICA E VIABILIDADE COMERCIAL DE DOCUMENTOS (RAG)",
        "=" * 80,
        f"Total de documentos analisados: {len(resultados)}",
        "",
    ]

    total_viaveis = 0
    for r in resultados:
        viavel = r.get("pode_usar_em_ia_comercial", False)
        if viavel:
            total_viaveis += 1
        status_icone = "[APROVADO]" if viavel else "[RESTRITO]"

        linhas.extend([
            f"Arquivo: {r['arquivo']}",
            f"  Status Comercial : {status_icone} - {r['status_comercial']}",
            f"  Título Oficial   : {r['titulo']}",
            f"  Titular/Origem   : {r['titular']} ({r['jurisdicao']})",
            f"  Total Páginas    : {r.get('paginas', 'N/A')}",
            f"  Enquadramento    : {r['enquadramento_legal']}",
            f"  Citação Padrão   : {r['recomendacao_citacao']}",
            f"  Restrições       : {r['restricoes']}",
            "-" * 80,
        ])

    linhas.extend([
        f"Conclusão: {total_viaveis}/{len(resultados)} documentos estão legalmente aptos para",
        "uso consultivo com citação em sistema comercial de IA (MetamorphIA).",
        "=" * 80,
    ])
    return "\n".join(linhas)


def main() -> None:
    """CLI para execução direta da verificação."""
    relatorio = gerar_relatorio_conformidade()
    print(relatorio)


if __name__ == "__main__":
    main()
