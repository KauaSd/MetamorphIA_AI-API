from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import AliasChoices, BaseModel, Field, model_validator
from diagnostico_utils import StatusDiagnostico


class AlunoDB(BaseModel):
    id_aluno: int
    nome_aluno: str
    neurodiv_aluno: str = Field(
        validation_alias=AliasChoices("neurodiv_aluno", "neuro_aluno")
    )
    desc_aluno: Optional[str] = None
    perfil_ia: Optional[str] = None
    indicadores_desenvolvimento: Optional[List[Dict[str, Any]]] = None
    metas_bimestre: Optional[List[Dict[str, Any]]] = None
    peis_disciplinas: List[Dict[str, Any]] = Field(default_factory=list)
    idade_aluno: Optional[int] = None
    id_prof: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_prof", "fk_Usuario_id_usuario")
    )
    id_turma: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_turma", "fk_Turma_id_turma")
    )
    pei_feito: bool = False
    pei_valido_ate: Optional[str] = None
    pei_ativo: Optional[bool] = None

    @model_validator(mode="after")
    def _resolver_pei_ativo(self) -> "AlunoDB":
        if self.pei_ativo is not None:
            return self
        if not self.pei_feito or not self.pei_valido_ate:
            self.pei_ativo = False
            return self
        try:
            dt = datetime.fromisoformat(self.pei_valido_ate.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            self.pei_ativo = dt > datetime.now(timezone.utc)
        except Exception:
            self.pei_ativo = False
        return self


class TurmaDB(BaseModel):
    id_turma: int
    nome_turma: Optional[str] = None
    serie_ano: Optional[str] = None
    letra: Optional[str] = None
    horario: Optional[str] = None
    id_prof: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_prof", "fk_Usuario_id_usuario")
    )
    id_escola: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("id_escola", "fk_Escola_id_escola")
    )


class AnexoItem(BaseModel):
    name: str
    type: Optional[str] = "application/octet-stream"
    size: Optional[int] = 0
    dataUrl: Optional[str] = None


class ChatRequest(BaseModel):
    id_prof: Optional[int] = None
    mensagem: str = ""
    conversa_id: Optional[str] = None
    historico: Optional[List[Dict[str, str]]] = None
    total_interacoes: Optional[int] = None
    anexos: Optional[List[AnexoItem]] = None
    gerar_arquivo_atividade: Optional[bool] = None
    modo_adaptacao: Optional[str] = None
    template_id: Optional[str] = None
    formato_saida: Optional[str] = "docx"
    instrucoes_ia: Optional[str] = None
    persistido_pelo_gateway: bool = False


class ChatResponse(BaseModel):
    resposta: str
    conversa_id: str
    perfil_atualizado: Optional[str] = None
    indicadores_desenvolvimento: Optional[List["IndicadorDesenvolvimentoItem"]] = None
    metas_bimestre: Optional[List["MetaBimestreItem"]] = None
    anexos: Optional[List[AnexoItem]] = None
    atividade_id: Optional[str] = None
    arquivo_nome: Optional[str] = None
    documento_docx_base64: Optional[str] = None
    documento_pdf_base64: Optional[str] = None
    download_url: Optional[str] = None
    opcoes_adaptacao: Optional[List[Dict[str, Any]]] = None
    modo_adaptacao_usado: Optional[str] = None
    template_usado: Optional[str] = None
    formato_arquivo: Optional[str] = None


class MensagemItem(BaseModel):
    role: str
    conteudo: str
    data: str
    anexos: Optional[List[AnexoItem]] = None


class ConversaResumo(BaseModel):
    conversa_id: str
    titulo: str
    data: str


class ConversaRecenteItem(BaseModel):
    conversa_id: str
    titulo: str
    data: str
    id_aluno: int
    nome_aluno: str
    neuro_aluno: str
    id_turma: Optional[int] = None
    nome_turma: Optional[str] = None


class ConversaDetalhe(BaseModel):
    conversa_id: str
    mensagens: List[MensagemItem]


class ConversaCompleta(ConversaRecenteItem):
    pei_ativo: bool = False
    mensagens: List[MensagemItem]


class DisciplinaItem(BaseModel):
    id: str
    nome: str


class PeiDisciplinaItem(BaseModel):
    id: str
    disciplina: str
    status: Literal["concluido", "em_andamento", "nao_iniciado"]


class PeisPorDisciplinaResumo(BaseModel):
    status_geral: str
    concluidos: int
    total: int
    atrasado: bool = False
    prazo_expirado: bool = False
    ultimo_envio: Optional[str] = None
    proximo_periodo: Optional[str] = None
    disciplinas: List[PeiDisciplinaItem] = Field(default_factory=list)


class MetaBimestreItem(BaseModel):
    id: str
    titulo: str
    status: Literal["em_progresso", "a_iniciar", "atingida"]


class IndicadorDesenvolvimentoItem(BaseModel):
    id: str
    titulo: str
    escala_esquerda: str
    escala_centro: str
    escala_direita: str
    nivel: int = Field(..., ge=1, le=5)
    cor: Literal["warning", "accent", "info", "success"]


class AlunoDescricaoBreveResponse(BaseModel):
    id_aluno: int
    nome: str
    idade: Optional[int] = None
    neurodivergencia: str
    turma: str
    descricao_breve: str


class AlunoDashboardResponse(BaseModel):
    id_aluno: int
    nome: str
    idade: Optional[int]
    turma: str
    diagnostico: str
    status_diagnostico: StatusDiagnostico
    desc_aluno: Optional[str] = None
    perfil_ia: Optional[str] = None
    descricao_breve: Optional[str] = None
    pei_gerado: bool
    atividades_adaptadas: int
    engajamento_percent: int
    desempenho_por_area: Dict[str, int]
    historico_conversas: List[ConversaResumo]
    peis_por_disciplina: Optional[PeisPorDisciplinaResumo] = None
    metas_bimestre: List[MetaBimestreItem] = Field(default_factory=list)
    indicadores_desenvolvimento: List[IndicadorDesenvolvimentoItem] = Field(default_factory=list)


class TurmaDashboardResponse(BaseModel):
    id_turma: int
    nome_turma: str
    total_alunos: int
    alunos_com_pei_ativo: int
    adaptacoes_feitas: int
    resumo: str
    engajamento_por_area: Dict[str, int]


class PeiImportarRequest(BaseModel):
    id_prof: Optional[int] = None
    arquivo_nome: str
    arquivo_base64: str
    componente_curricular: Optional[str] = "Língua Portuguesa"
    periodo_bimestre: Optional[int] = 1
    persistido_pelo_gateway: bool = False


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
    id_prof: Optional[int] = None
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
    id_prof: Optional[int] = None
    respostas: List[PeiRespostaItem] = Field(default_factory=list)
    nome_professor_regente: Optional[str] = None
    nome_professor_especializado: Optional[str] = None
    componente_curricular: Optional[str] = "Língua Portuguesa"
    ano_escolar: Optional[str] = "Ensino Fundamental"
    periodo_bimestre: Optional[int] = 1
    descricao_aluno: Optional[str] = None
    persistido_pelo_gateway: bool = False


class PeiCriarResponse(BaseModel):
    pei_id: str
    documento_gerado: str
    data: str
    formato: str = "docx"
    arquivo_nome: Optional[str] = None
    respostas_gerais: Optional[Dict[str, str]] = None
    documento_docx_base64: Optional[str] = None
    download_url: Optional[str] = None
    perfil_atualizado: Optional[str] = None
    indicadores_desenvolvimento: Optional[List[IndicadorDesenvolvimentoItem]] = None
    metas_bimestre: Optional[List[MetaBimestreItem]] = None
    pei_feito: bool = True
    pei_valido_ate: Optional[str] = None


class PeiResumo(BaseModel):
    pei_id: str
    data: str
    resumo: str


class PeiDetalhe(BaseModel):
    pei_id: str
    id_aluno: int
    documento_gerado: str
    data: str


class PerfilGerarRequest(BaseModel):
    historico_conversas: Optional[str] = None
    persistido_pelo_gateway: bool = False


class PerfilGerarResponse(BaseModel):
    id_aluno: int
    perfil_gerado: str
    sincronizado_api_db: bool
    indicadores_desenvolvimento: Optional[List[IndicadorDesenvolvimentoItem]] = None
    metas_bimestre: Optional[List[MetaBimestreItem]] = None
