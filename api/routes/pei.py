from __future__ import annotations

import base64
import copy
import glob
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import uuid
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from auth_jwt import verificar_token_jwt
from config import get_settings
from db_client import ResourceNotFoundError, atualizar_descricao_aluno, obter_aluno
from guardrails import sanitizar_dados_sensiveis
from llm_client import gerar_resposta
from memoria.chroma_client import salvar_documento
from memoria.queries import (
    buscar_contexto_aluno,
    buscar_documentos_referencia,
    listar_peis_aluno,
    obter_pei_por_id,
    obter_ultimo_perfil,
)
from schemas import (
    PeiCriarRequest,
    PeiCriarResponse,
    PeiDetalhe,
    PeiInicialRequest,
    PeiInicialResponse,
    PeiPerguntaGeralRoteiro,
    PeiRespostaItem,
    PeiResumo,
    PeiSubperguntaItem,
)

router = APIRouter(tags=["pei"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PEI_DOCX_MEMORIA: dict[str, bytes] = {}

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
    ultimo_perfil_memoria: Optional[str],
    descricao_enviada: Optional[str] = None,
) -> str:
    partes = []
    for texto in (descricao_enviada, perfil_ia, desc_aluno, ultimo_perfil_memoria):
        if texto and texto.strip() and texto.strip().lower() not in ("string", "none", "null"):
            limpo, _ = sanitizar_dados_sensiveis(texto.strip())
            if limpo and limpo not in partes:
                partes.append(limpo)
    return "\n".join(partes).strip()


def _opcoes_padrao_contextualizadas(
    nome_aluno: str,
    neurodivergencia: str,
    contexto_aluno: str,
    componente_curricular: str,
    ano_escolar: str,
    periodo_bimestre: int,
) -> tuple[str, dict[str, list[str]]]:
    resumo_ctx = (
        contexto_aluno[:140].rstrip(".")
        if contexto_aluno
        else f"perfil {neurodivergencia}"
    )

    conteudo_previsto = (
        f"Currículo Paulista ({ano_escolar} - {periodo_bimestre}º Bimestre - {componente_curricular}): "
        f"desenvolvimento das habilidades estruturantes do bimestre em {componente_curricular}, "
        f"priorizando compreensão conceitual, resolução de situações-problema práticas, leitura e "
        f"interpretação multimodal alinhadas à turma regular de {nome_aluno}."
    )

    opcoes_por_sub: dict[str, list[str]] = {
        "1.1": [
            f"Habilidades consolidadas: {nome_aluno} compreende comandos curtos e realiza atividades apoiadas em recursos visuais e temas de seu interesse ({resumo_ctx}).",
            f"Em desenvolvimento: participa das tarefas de {componente_curricular} quando fragmentadas em etapas menores, mantendo o foco por períodos curtos.",
            f"Com mediação: realiza registros escritos e atividades abstratas de {componente_curricular} com apoio individual e exemplos concretos.",
            f"Autonomia funcional: expressa compreensão oralmente ou por apontamento visual, necessitando de suporte apenas na organização sequencial da tarefa.",
        ],
        "1.2": [
            f"Simplificar os enunciados de {componente_curricular} e dividir cada habilidade do {periodo_bimestre}º bimestre em pequenas etapas sequenciais.",
            f"Aumentar o tempo de realização das atividades em sala e reduzir a quantidade de itens por folha, sem retirar o conteúdo curricular da turma.",
            f"Utilizar recursos visuais e materiais concretos para representar os conceitos essenciais de {componente_curricular} previstos no Currículo Paulista.",
            f"Priorizar as habilidades estruturantes do {ano_escolar} com instruções diretas e verificação passo a passo.",
        ],
        "1.3": [
            f"Com uso de materiais adaptados, apoio visual passo a passo e linguagem clara e objetiva nas aulas de {componente_curricular}.",
            f"Por meio de recursos de acessibilidade e tecnologia assistiva (tablet/computador ou pranchas visuais) aliados ao suporte do professor regente.",
            f"Articulando o planejamento de {componente_curricular} com o professor especializado do AEE para pré-ensino e reforço das habilidades do bimestre.",
            f"Combinando adaptação de material impresso (fonte ampliada e destaque visual) com mediação individual nos momentos de maior exigência cognitiva.",
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
            "Disponibilizar material com fonte acessível, espaçamento ampliado, alto contraste e organização visual limpa.",
            "Utilizar figuras, esquemas visuais, pictogramas e organizadores gráficos para apoiar a compreensão em sala.",
            "Permitir o uso de tablet ou computador para consulta e registro das respostas nas aulas.",
            "Garantir adequação ambiental e sensorial (assento estratégico, abafadores de ruído quando necessário e tecnologias assistivas).",
        ],
        "3.1": [
            "Observação diária sistematizada no caderno do professor combinada com portfólio das atividades adaptadas do estudante.",
            "Avaliações práticas e provas adaptadas com apoio visual, priorizando a evolução do estudante em relação a ele mesmo.",
            "Lista de verificação (checklist) de habilidades do bimestre e rubricas com critérios claros e graduados de aprendizagem.",
            "Conversas orientadas (avaliação oral), produções visuais/áudio e autoavaliação mediada com apoio visual (cores/ícones).",
        ],
        "4.1": [
            f"Vídeos: animações curtas e vídeos com passo a passo visual e linguagem simples sobre os temas de {componente_curricular}.",
            f"Livros: materiais paradidáticos com linguagem acessível, histórias curtas ilustradas e livros em formato ampliado/digital sobre o tema estudado.",
            f"Jogos: jogos de memória, associação (imagem–palavra / causa–efeito), quebra-cabeças e jogos digitais educativos de {componente_curricular}.",
            "Atividades e exercícios: sequências didáticas curtas, atividades manipulativas com materiais concretos e exercícios multissensoriais de ligar, ordenar e montar.",
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
        prompt_opcoes = (
            f"Você está gerando as opções de resposta para o formulário inicial do PEI (Roteiro MetamorphIA).\n"
            f"Estudante: {nome_aluno} | Neurodivergência: {neurodivergencia}\n"
            f"Contexto do estudante (descrição feita pelo professor): {contexto_limpo}\n"
            f"Componente Curricular: {componente_curricular} | Ano: {ano_escolar} | Bimestre: {periodo_bimestre}º\n\n"
            f"Para cada subpergunta abaixo, gere EXATAMENTE 4 possíveis respostas curtas, práticas e personalizadas para o contexto deste estudante, "
            f"seguindo os critérios em vermelho do Roteiro MetamorphIA:\n"
            f"- '1.1': Subpergunta 'O que o estudante já consegue fazer?' (Critérios: habilidades já consolidadas; o que ainda está em desenvolvimento; o que o estudante só realiza com ajuda).\n"
            f"- '1.2': Subpergunta 'O que precisa e é possível de ser ajustado esse bimestre?' (Critérios: simplifica ações, aumenta o tempo de trabalho, divide a habilidade em pequenas etapas, usa recursos acessíveis sem retirar o conteúdo curricular).\n"
            f"- '1.3': Subpergunta 'E como isso será feito?' (Critérios: recursos de acessibilidade, uso de tecnologias assistivas, apoio de profissionais quando houver, adaptações de materiais e linguagem).\n"
            f"- '2.1': Subpergunta 'Quais estratégias pedagógicas você utilizará para que o estudante consiga participar das aulas?' (Critérios: explicar em partes menores, exemplos concretos/cotidiano, mais tempo, mediação em grupo, retomar conteúdos, previsibilidade/rotina visual, instruções claras com apoio visual).\n"
            f"- '2.2': Subpergunta 'Quais intervenções pedagógicas você utilizará para que o estudante consiga participar das aulas?' (Critérios: apoio individual em atividades complexas, adaptação de quantidade, adaptação de complexidade, ajuste da forma de participação oral/pictograma/escrita/digital).\n"
            f"- '2.3': Subpergunta 'Quais recursos de acessibilidade você utilizará para que o estudante consiga participar das aulas?' (Critérios: material ampliado/contrastes/fontes acessíveis, figuras/esquemas/pictogramas/CAA, uso de tablet/computador, fones/abafadores/mobiliário adaptado, tecnologias assistivas).\n"
            f"- '3.1': Subpergunta 'Como você vai avaliar o estudante respeitando seu ritmo, suas necessidades e suas formas de expressão?' (Critérios: observação diária, portfólio, avaliações práticas, checklist de habilidades, registro fotográfico, autoavaliação com apoio visual, conversas orientadas, produções em vídeo/áudio/desenho, rubricas; avaliando o progresso do aluno em relação a ele mesmo).\n"
            f"- '4.1': Subpergunta 'Quais vídeos, livros, jogos, exercícios ou outras atividades você indicará para apoiar e fortalecer o aprendizado do estudante?' (Critérios: 1 opção de Vídeos, 1 opção de Livros, 1 opção de Jogos e 1 opção de Atividades/exercícios adaptados ao componente curricular e ao aluno).\n\n"
            f"Retorne APENAS um objeto JSON válido com a chave 'conteudo_previsto' (string descrevendo o conteúdo previsto do Currículo Paulista "
            f"para este ano/bimestre em {componente_curricular}) e a chave 'subperguntas' contendo as chaves '1.1', '1.2', '1.3', '2.1', '2.2', '2.3', '3.1', '4.1', "
            f"onde cada chave possui uma lista de EXATAMENTE 4 strings."
        )
        try:
            resposta_llm = gerar_resposta(prompt=prompt_opcoes, aplicar_guardrails=False)
            inicio = resposta_llm.find("{")
            fim = resposta_llm.rfind("}")
            if inicio != -1 and fim != -1 and fim > inicio:
                dados_json = json.loads(resposta_llm[inicio : fim + 1])
                if isinstance(dados_json.get("conteudo_previsto"), str) and dados_json["conteudo_previsto"].strip():
                    conteudo_previsto = dados_json["conteudo_previsto"].strip()
                subs_json = dados_json.get("subperguntas", {})
                if isinstance(subs_json, dict):
                    for k in list(opcoes_por_sub.keys()):
                        lista_cand = subs_json.get(k)
                        if isinstance(lista_cand, list):
                            limpas = [str(x).strip() for x in lista_cand if str(x).strip()]
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
                    pergunta="Quais estratégias pedagógicas você utilizará para que o estudante consiga participar das aulas?",
                    opcoes=opcoes_por_sub["2.1"][:4],
                    alternativas_fixas=["Não irei utilizar estratégias pedagógicas", "Outros"],
                ),
                PeiSubperguntaItem(
                    id_subpergunta="2.2",
                    pergunta="Quais intervenções pedagógicas você utilizará para que o estudante consiga participar das aulas?",
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
        perg_limpa, _ = sanitizar_dados_sensiveis(item.pergunta)
        resp_limpa, _ = sanitizar_dados_sensiveis(item.resposta)
        num_geral = _classificar_pergunta_geral(item, idx)
        agrupadas[num_geral].append(f"- {perg_limpa}: {resp_limpa}")

    todos_dados_brutos = "\n".join(
        f"- {sanitizar_dados_sensiveis(r.pergunta)[0]}: {sanitizar_dados_sensiveis(r.resposta)[0]}"
        for r in respostas_subperguntas
    )

    dados_p1 = "\n".join(agrupadas[1]) or todos_dados_brutos or "Sem subperguntas específicas informadas."
    dados_p2 = "\n".join(agrupadas[2]) or todos_dados_brutos or "Sem subperguntas específicas informadas."
    dados_p3 = "\n".join(agrupadas[3]) or todos_dados_brutos or "Sem subperguntas específicas informadas."
    dados_p4 = "\n".join(agrupadas[4]) or todos_dados_brutos or "Sem subperguntas específicas informadas."

    respostas_padrao: dict[int, str] = {
        1: (
            f"Conteúdos e Habilidades do Currículo Paulista ({ano_escolar} - {periodo_bimestre}º Bimestre - {componente_curricular}):\n"
            f"Serão trabalhadas as habilidades estruturantes previstas no Currículo da Rede Estadual Paulista para a turma regular em {componente_curricular}, "
            f"assegurando o acesso de {nome_aluno} ({neurodivergencia}) ao conteúdo curricular sem redução de expectativas.\n"
            f"• Habilidades consolidadas, em desenvolvimento e com mediação (dados levantados): {dados_p1.replace(chr(10), ' | ')}\n"
            f"• Adaptações necessárias no bimestre: simplificação de comandos e ações, ampliação do tempo de realização e divisão das habilidades em pequenas etapas sequenciais.\n"
            f"• Recursos e apoios previstos: uso de recursos de acessibilidade, materiais adaptados com suporte visual, tecnologias assistivas e articulação contínua com o professor especializado do Atendimento Educacional Especializado (AEE)."
        ),
        2: (
            f"Fundamentado nos princípios do Desenho Universal para a Aprendizagem (DUA - múltiplos meios de engajamento, representação e ação/expressão) "
            f"e na articulação entre sala comum, AEE e gestão escolar:\n"
            f"• Estratégias, Intervenções e Recursos selecionados para {nome_aluno}: {dados_p2.replace(chr(10), ' | ')}\n"
            f"• Estratégias Pedagógicas: explicação do conteúdo em partes menores, uso de exemplos concretos do cotidiano, previsibilidade com rotina visual passo a passo, ampliação de tempo e mediação em atividades coletivas.\n"
            f"• Intervenções Pedagógicas: apoio individualizado em tarefas complexas, adaptação de quantidade e complexidade mantendo o objetivo curricular e flexibilização da forma de participação (oral, pictograma, escrita ou digital).\n"
            f"• Recursos de Acessibilidade: materiais com fonte acessível e destaque visual, esquemas/pictogramas, recursos digitais e controle de estímulos sensoriais."
        ),
        3: (
            f"O acompanhamento da aprendizagem de {nome_aluno} será processual, contínuo e centrado em suas potencialidades, "
            f"utilizando instrumentos diversificados e acessíveis:\n"
            f"• Instrumentos definidos pelo professor: {dados_p3.replace(chr(10), ' | ')}\n"
            f"• Aplicação Inclusiva: observação diária registrada pelo professor, portfólio com produções e atividades adaptadas, "
            f"avaliações práticas e orais orientadas, lista de verificação (checklist) de habilidades, autoavaliação mediada com apoio visual e provas adaptadas com recursos de acessibilidade.\n"
            f"• Princípio Básico de Avaliação: mensurar e valorizar o progresso individual de {nome_aluno} em relação a ele mesmo e ao seu ponto de partida no bimestre, orientando o replanejamento pedagógico."
        ),
        4: (
            f"Recursos pedagógicos complementares e suplementares indicados para fortalecer o aprendizado de {nome_aluno} em {componente_curricular}:\n"
            f"• Direcionamento a partir das subperguntas: {dados_p4.replace(chr(10), ' | ')}\n"
            f"• Vídeos: animações curtas explicando o conteúdo do {periodo_bimestre}º bimestre, vídeos com linguagem simples, narração clara, exemplos visuais e passo a passo.\n"
            f"• Livros: textos e histórias curtas ilustradas, livros de curiosidades sobre o tema da aula e materiais paradidáticos em formatos acessíveis/ampliados.\n"
            f"• Jogos: jogos de memória temáticos, jogos de associação (imagem–palavra e causa–efeito), jogos digitais educativos, quebra-cabeças e jogos de turnos para interação social.\n"
            f"• Atividades e Exercícios: sequências didáticas com etapas curtas, atividades manipulativas com materiais concretos, exercícios de completar/ligar/ordenar e propostas multissensoriais."
        ),
    }

    settings = get_settings()
    if settings.LLM_PROVIDER.lower() != "mock":
        prompt_geral = (
            f"Você está redigindo as 4 respostas finais do Plano Educacional Individualizado (PEI - Anexo IV Resolução SEDUC nº 129/2025).\n"
            f"Estudante: {nome_aluno} | Idade: {idade_aluno or 'Não informada'} | Neurodivergência: {neurodivergencia}\n"
            f"Componente Curricular: {componente_curricular} | Ano: {ano_escolar} | Bimestre: {periodo_bimestre}º\n"
            f"Contexto do Estudante: {contexto_aluno}\n\n"
            f"REGRA OBRIGATÓRIA:\n"
            f"- Gere a resposta APENAS para cada uma das 4 Perguntas Gerais (NÃO repita as subperguntas como perguntas no texto final e NÃO inclua textos explicativos azuis).\n"
            f"- Considere todas as respostas das subperguntas como dados para construir a resposta de cada Pergunta Geral, seguindo rigorosamente o norteamento em vermelho.\n\n"
            f"Pergunta Geral 1: {PERGUNTAS_GERAIS_MODELO[1]}\n"
            f"Norteamento obrigatório 1: {NORTEAMENTO_VERMELHO_PERGUNTAS_GERAIS[1]}\n"
            f"Dados das subperguntas 1:\n{dados_p1}\n\n"
            f"Pergunta Geral 2: {PERGUNTAS_GERAIS_MODELO[2]}\n"
            f"Norteamento obrigatório 2: {NORTEAMENTO_VERMELHO_PERGUNTAS_GERAIS[2]}\n"
            f"Dados das subperguntas 2:\n{dados_p2}\n\n"
            f"Pergunta Geral 3: {PERGUNTAS_GERAIS_MODELO[3]}\n"
            f"Norteamento obrigatório 3: {NORTEAMENTO_VERMELHO_PERGUNTAS_GERAIS[3]}\n"
            f"Dados das subperguntas 3:\n{dados_p3}\n\n"
            f"Pergunta Geral 4: {PERGUNTAS_GERAIS_MODELO[4]}\n"
            f"Norteamento obrigatório 4: {NORTEAMENTO_VERMELHO_PERGUNTAS_GERAIS[4]}\n"
            f"Dados das subperguntas 4:\n{dados_p4}\n\n"
            f"Retorne APENAS um JSON válido com as chaves '1', '2', '3' e '4', contendo o texto completo e bem redigido de cada resposta geral."
        )
        try:
            texto_ia = gerar_resposta(
                prompt=prompt_geral,
                contexto_rag=contexto_rag,
                fontes_referencia=fontes_docs,
                aplicar_guardrails=False,
            )
            ini = texto_ia.find("{")
            fim = texto_ia.rfind("}")
            if ini != -1 and fim != -1 and fim > ini:
                parsed = json.loads(texto_ia[ini : fim + 1])
                for num in (1, 2, 3, 4):
                    val = parsed.get(str(num)) or parsed.get(f"pergunta_{num}")
                    if isinstance(val, str) and len(val.strip()) > 20:
                        respostas_padrao[num] = sanitizar_dados_sensiveis(val.strip())[0]
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


@router.post("/alunos/{id_aluno}/pei/inicial", response_model=PeiInicialResponse)
async def criar_pei_inicial(
    id_aluno: int,
    payload: Optional[PeiInicialRequest] = None,
    usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PeiInicialResponse:
    """Inicia a elaboração do PEI com base no Roteiro MetamorphIA.docx.

    - Se o aluno ainda não tiver contexto (1º PEI) e nenhuma descrição for enviada no body,
      retorna a pergunta pedindo ao professor que descreva o estudante em pelo menos 5 linhas.
    - Se o aluno já tiver contexto (ou a descrição for enviada), retorna todas as perguntas e
      subperguntas do roteiro com 4 possíveis respostas geradas para cada subpergunta.
    """
    req = payload or PeiInicialRequest()
    try:
        aluno = await obter_aluno(id_aluno)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aluno {id_aluno} não encontrado na base de dados.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro de comunicação com a API-DB: {exc}",
        )

    if req.descricao_aluno and req.descricao_aluno.strip():
        desc_limpa, _ = sanitizar_dados_sensiveis(req.descricao_aluno.strip())
        try:
            await atualizar_descricao_aluno(id_aluno, desc_limpa)
        except Exception:
            pass
    else:
        desc_limpa = None

    ultimo_perfil = obter_ultimo_perfil(id_aluno)
    contexto_aluno = _obter_contexto_consolidado_aluno(
        desc_aluno=aluno.desc_aluno,
        perfil_ia=aluno.perfil_ia,
        ultimo_perfil_memoria=ultimo_perfil,
        descricao_enviada=desc_limpa,
    )

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(aluno.neurodiv_aluno)

    return gerar_roteiro_pei_inicial(
        id_aluno=id_aluno,
        nome_aluno=nome_aluno_limpo,
        neurodivergencia=neuro_limpa,
        contexto_aluno=contexto_aluno,
        componente_curricular=req.componente_curricular or "Língua Portuguesa",
        ano_escolar=req.ano_escolar or "Ensino Fundamental",
        periodo_bimestre=req.periodo_bimestre or 1,
        indice_subpergunta=req.indice_subpergunta,
    )


async def _processar_criacao_pei(
    id_aluno: int,
    payload: PeiCriarRequest,
    usuario_autenticado: dict[str, Any],
) -> tuple[PeiCriarResponse, bytes]:
    try:
        aluno = await obter_aluno(id_aluno)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aluno {id_aluno} não encontrado na base de dados.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro de comunicação com a API-DB: {exc}",
        )

    id_prof = payload.id_prof or usuario_autenticado.get("id_prof", 1)
    id_turma = aluno.id_turma or 0

    for item in payload.respostas:
        perg_limpa, _ = sanitizar_dados_sensiveis(item.pergunta)
        resp_limpa, _ = sanitizar_dados_sensiveis(item.resposta)
        salvar_documento(
            id_aluno=id_aluno,
            id_turma=id_turma,
            id_prof=id_prof,
            tipo="pei_resposta",
            conteudo=f"{perg_limpa} => {resp_limpa}",
        )

    ultimo_perfil = obter_ultimo_perfil(id_aluno)
    contexto_historico = buscar_contexto_aluno(
        id_aluno, "dificuldades aprendizagem objetivos adaptações PEI"
    )
    contexto_aluno = _obter_contexto_consolidado_aluno(
        desc_aluno=aluno.desc_aluno,
        perfil_ia=aluno.perfil_ia,
        ultimo_perfil_memoria=ultimo_perfil,
        descricao_enviada=payload.descricao_aluno,
    )
    if contexto_historico:
        contexto_aluno = f"{contexto_aluno}\n{contexto_historico}".strip()

    fontes_docs = buscar_documentos_referencia(
        query="diretrizes elaboração PEI adaptações curriculares acessibilidade AEE Decreto 12686 DUA",
        n_results=3,
    )
    textos_rag = "\n\n".join(f"[{f['titulo']}]: {f['texto']}" for f in fontes_docs)

    nome_aluno_limpo, _ = sanitizar_dados_sensiveis(aluno.nome_aluno)
    neuro_limpa, _ = sanitizar_dados_sensiveis(aluno.neurodiv_aluno)
    prof_regente, _ = sanitizar_dados_sensiveis(
        payload.nome_professor_regente or usuario_autenticado.get("nome_prof", "") or ""
    )
    prof_esp, _ = sanitizar_dados_sensiveis(payload.nome_professor_especializado or "")
    componente, _ = sanitizar_dados_sensiveis(payload.componente_curricular or "Língua Portuguesa")
    ano_escolar, _ = sanitizar_dados_sensiveis(payload.ano_escolar or "Ensino Fundamental")
    bimestre = payload.periodo_bimestre if payload.periodo_bimestre in (1, 2, 3, 4) else 1

    respostas_gerais_int = gerar_respostas_gerais_pei(
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

    pei_id = str(uuid.uuid4())
    data_iso = datetime.now(timezone.utc).isoformat()
    _PEI_DOCX_MEMORIA[pei_id] = docx_bytes

    salvar_documento(
        id_aluno=id_aluno,
        id_turma=id_turma,
        id_prof=id_prof,
        tipo="pei_documento",
        conteudo=documento_gerado,
        id_doc=pei_id,
        data_iso=data_iso,
        titulo=f"PEI - {nome_aluno_limpo} - {data_iso[:10]}",
    )

    nome_arquivo_seguro = "".join(
        c if c.isalnum() or c in ("_", "-") else "_" for c in nome_aluno_limpo
    ).strip("_") or f"aluno_{id_aluno}"
    arquivo_nome = f"PEI_{nome_arquivo_seguro}_{bimestre}Bim_2026.docx"

    resposta_obj = PeiCriarResponse(
        pei_id=pei_id,
        documento_gerado=documento_gerado,
        data=data_iso,
        formato="docx",
        arquivo_nome=arquivo_nome,
        respostas_gerais={
            PERGUNTAS_GERAIS_MODELO[k]: v for k, v in respostas_gerais_int.items()
        },
        documento_docx_base64=base64.b64encode(docx_bytes).decode("ascii"),
        download_url=f"/alunos/{id_aluno}/pei/{pei_id}/docx",
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
    """Gera o PEI do aluno seguindo o modelo 2026 (.docx) e a lógica do Roteiro MetamorphIA."""
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
    """Gera e retorna diretamente o arquivo .docx do PEI no estilo do modelo 2026."""
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
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> Response:
    """Baixa o arquivo .docx de um PEI já gerado."""
    docx_bytes = _PEI_DOCX_MEMORIA.get(pei_id)
    if not docx_bytes:
        pei = obter_pei_por_id(id_aluno, pei_id)
        if not pei:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PEI não encontrado para este aluno.",
            )
        texto = pei.get("documento_gerado", "")
        docx_bytes = montar_docx_pei(
            nome_aluno=f"Aluno {id_aluno}",
            respostas_gerais={1: texto, 2: texto, 3: texto, 4: texto},
        )

    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="PEI_aluno_{id_aluno}_{pei_id[:8]}.docx"',
            "X-Pei-Id": pei_id,
        },
    )


@router.get("/alunos/{id_aluno}/pei", response_model=list[PeiResumo])
async def get_peis_aluno(
    id_aluno: int,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> list[PeiResumo]:
    """Lista todos os PEIs registrados para o estudante."""
    peis = listar_peis_aluno(id_aluno)
    return [PeiResumo(**p) for p in peis]


@router.get("/alunos/{id_aluno}/pei/{pei_id}", response_model=PeiDetalhe)
async def get_pei_detalhe(
    id_aluno: int,
    pei_id: str,
    _usuario_autenticado: dict[str, Any] = Depends(verificar_token_jwt),
) -> PeiDetalhe:
    """Busca o conteúdo na íntegra de um PEI específico."""
    pei = obter_pei_por_id(id_aluno, pei_id)
    if not pei:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PEI não encontrado para este aluno.",
        )
    return PeiDetalhe(**pei)
