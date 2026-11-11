"""Configurações centralizadas da aplicação API-IA MetamorphIA.

Carrega variáveis de ambiente via dotenv e define valores padrão seguros
para conexão com a API-DB, banco vetorial ChromaDB, chaves dos modelos de LLM
e autenticação JWT compartilhada com a API-DB.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    # Conexão com a API do banco relacional
    DB_API_URL: str = "http://localhost:8000"
    API_DB_AUTH_TOKEN: Optional[str] = None

    # Autenticação JWT (mesma chave e algoritmo da api_db para validação cruzada)
    SECRET_KEY: str = "sua_chave_secreta_super_segura_metamorphia_2026"
    ALGORITHM: str = "HS256"

    # Persistência do banco vetorial ChromaDB
    CHROMA_PATH: str = "./data/chroma"
    CHROMA_COLLECTION: str = "metamorphia_memoria"
    CHROMA_COLLECTION_MEMORIA: str = "metamorphia_memoria"
    CHROMA_COLLECTION_DOCS: str = "metamorphia_documentos"
    RAW_DATA_PATH: str = "./data/raw"

    # Provedores de Inteligência Artificial
    LLM_PROVIDER: str = "gemini"  # gemini | groq | auto | mock
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    GEMINI_MAX_RPM: int = 15
    GEMINI_MAX_RPD: int = 500

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância singleton de configurações da aplicação."""
    return Settings()
