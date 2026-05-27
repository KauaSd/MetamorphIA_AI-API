#Define como sai as respostas da api
#É usado para não gastar processamento com saidas erradas
from pydantic import BaseModel
class AlunoResponse(BaseModel):
    nome: str
    idade: int | None
    serie: str
    neurodivergencia: str
    diagnosticado: bool | None
    descricao: str