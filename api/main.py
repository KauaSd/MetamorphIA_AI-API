#Quem vai fazer a API funcionar
from fastapi import FastAPI

app=FastAPI()

from api.routes.query import Queryrouter

app.include_router(Queryrouter)

#pra rodar a API use: python -m uvicorn api.main:app --reload