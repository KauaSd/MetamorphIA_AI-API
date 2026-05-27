#Recebe a mensagem do front (ou da gente por enquanto), chama o chain, que devolve a mensagem e o salvamento no banco relacional 
from fastapi import APIRouter
from src.chain import resposta
import os
from dotenv import load_dotenv
import json
from api.schemas.response import AlunoResponse
load_dotenv()
Queryrouter = APIRouter(prefix="/query", tags= ["query"])

@Queryrouter.get("/enviaTxtIA/{pergunta}")
async def enviaTxtIA(pergunta: str):
    return {"resposta" : resposta(pergunta)}
    

@Queryrouter.get("/Envia/{DescAl}", response_model=AlunoResponse)
async def enviaInfoAluno(DescAl: str):
    result = resposta(f"""
Gere um JSON com as seguintes informações de um aluno. Retorne APENAS o JSON, sem explicações, sem texto adicional, sem blocos de código markdown.

Campos obrigatórios (use string vazia "" se não souber ou não houver informação):
- "nome": nome completo do aluno
- "idade": idade do aluno (número inteiro, ou null se não souber)
- "serie": série/ano escolar do aluno
- "neurodivergencia": diagnóstico de neurodivergência do aluno (ex: TDAH, TEA, Dislexia)
- "diagnosticado": true se tiver diagnóstico confirmado, false se for suspeita do professor, ou null se não houver nenhuma indicação
- "descricao": descrição geral do aluno com foco em comportamento e aprendizado, sem repetir a neurodivergência já informada no campo anterior

Informações disponíveis:
{DescAl}

Exemplo de saída esperada:
{{"nome":"João Silva","idade":10,"serie":"5º ano","neurodivergencia":"TDAH","diagnosticado":true,"descricao":"Aluno comunicativo..."}}

Caso algum campo não tenha informação, use "" para textos e null para idade.
""")
    return json.loads(result)