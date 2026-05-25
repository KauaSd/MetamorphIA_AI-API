#Recebe a mensagem do front (ou da gente por enquanto), chama o chain, que devolve a mensagem e o salvamento no banco relacional 
from fastapi import APIRouter
from src.chain import resposta
import os
from dotenv import load_dotenv
load_dotenv()
Queryrouter = APIRouter(prefix="/query", tags= ["query"])

@Queryrouter.get("/enviaTxtIA/{pergunta}")
async def enviaTxtIA(pergunta: str):
    return {"resposta" : resposta(pergunta)}
    
