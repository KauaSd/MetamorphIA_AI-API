from typing import Dict, List, Optional
from pydantic import AliasChoices, BaseModel, Field
from diagnostico_utils import StatusDiagnostico


class AlunoDB(BaseModel):
    id_aluno: int
    nome_aluno: str
    neurodiv_aluno: str = Field(
        validation_alias=AliasChoices("neurodiv_aluno", "neuro_aluno")
    )
    desc_aluno: Optional[str] = None
    perfil_ia: Optional[str] = None
    idade_aluno: Optional[int] = None
    id_prof: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_prof", "fk_Usuario_id_usuario")
    )
    id_turma: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_turma", "fk_Turma_id_turma")
    )


class TurmaDB(BaseModel):
    id_turma: int
    nome_turma: Optional[str] = None
    id_prof: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_prof", "fk_Usuario_id_usuario")
    )
    id_escola: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_escola", "fk_Escola_id_escola")
    )


class ChatRequest(BaseModel):
    id_prof: int
    mensagem: str
    conversa_id: Optional[str] = None
    historico: Optional[List[Dict[str, str]]] = None
    total_interacoes: Optional[int] = None


class ChatResponse(BaseModel):
    resposta: str
    conversa_id: str
    perfil_atualizado: Optional[str] = None


class MensagemItem(BaseModel):
    role: str
    conteudo: str
    data: str


class ConversaResumo(BaseModel):
    conversa_id: str
    titulo: str
    data: str


class ConversaDetalhe(BaseModel):
    conversa_id: str
    mensagens: List[MensagemItem]


class AlunoDashboardResponse(BaseModel):
    id_aluno: int
    nome: str
    idade: Optional[int]
    turma: str
    diagnostico: str
    status_diagnostico: StatusDiagnostico
    perfil_ia: Optional[str] = None
    pei_gerado: bool
    atividades_adaptadas: int
    engajamento_percent: int
    desempenho_por_area: Dict[str, int]
    historico_conversas: List[ConversaResumo]


class PeiSubperguntaItem(BaseModel):
    id_subpergunta: str
    pergunta: str
    opcoes: List[str] = Field(
        default_factory=list,
        description="4 possíveis respostas geradas pela IA com base no contexto do aluno",
    )
    alternativas_fixas: List[str] = Field(
        default_factory=list,
        description="Alternativas fixas para a interface do usuário (ex: 'não irei utilizar...' e 'outros')",
    )


class PeiPerguntaGeralRoteiro(BaseModel):
    numero: int
    pergunta_geral: str
    info_usuario: Optional[str] = None
    conteudo_previsto_curriculo: Optional[str] = None
    minimo_subperguntas: int = 1
    subperguntas: List[PeiSubperguntaItem]


class PeiInicialRequest(BaseModel):
    id_prof: Optional[int] = 1
    descricao_aluno: Optional[str] = Field(
        default=None,
        description="Descrição do estudante em pelo menos 5 linhas (quando for o primeiro PEI e não houver contexto prévio)",
    )
    componente_curricular: Optional[str] = "Língua Portuguesa"
    ano_escolar: Optional[str] = "Ensino Fundamental"
    periodo_bimestre: Optional[int] = 1
    indice_subpergunta: Optional[int] = Field(
        default=None,
        description="Índice opcional (0 a N-1) para avançar subpergunta por subpergunta até o fim",
    )


class PeiInicialResponse(BaseModel):
    id_aluno: int
    tem_contexto: bool
    precisa_descricao_aluno: bool
    pergunta_inicial_aluno: Optional[str] = None
    tipo_campo: Optional[str] = None
    contexto_aluno_utilizado: Optional[str] = None
    indice_subpergunta_atual: Optional[int] = None
    total_subperguntas: int = 0
    subpergunta_atual: Optional[PeiSubperguntaItem] = None
    perguntas: List[PeiPerguntaGeralRoteiro] = Field(default_factory=list)


class PeiRespostaItem(BaseModel):
    pergunta: str
    resposta: str
    numero_pergunta_geral: Optional[int] = None


class PeiCriarRequest(BaseModel):
    id_prof: Optional[int] = 1
    respostas: List[PeiRespostaItem] = Field(default_factory=list)
    nome_professor_regente: Optional[str] = None
    nome_professor_especializado: Optional[str] = None
    componente_curricular: Optional[str] = "Língua Portuguesa"
    ano_escolar: Optional[str] = "Ensino Fundamental"
    periodo_bimestre: Optional[int] = 1
    descricao_aluno: Optional[str] = None


class PeiCriarResponse(BaseModel):
    pei_id: str
    documento_gerado: str
    data: str
    formato: str = "docx"
    arquivo_nome: Optional[str] = None
    respostas_gerais: Optional[Dict[str, str]] = None
    documento_docx_base64: Optional[str] = None
    download_url: Optional[str] = None


class PeiResumo(BaseModel):
    pei_id: str
    data: str
    resumo: str


class PeiDetalhe(BaseModel):
    pei_id: str
    id_aluno: int
    documento_gerado: str
    data: str


class PerfilGerarResponse(BaseModel):
    id_aluno: int
    perfil_gerado: str
    sincronizado_api_db: bool
