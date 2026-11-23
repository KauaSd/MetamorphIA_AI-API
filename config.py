"""Configurações centralizadas da aplicação API-IA MetamorphIA.

Carrega variáveis de ambiente via dotenv e define valores padrão seguros
para conexão com a API-DB, banco vetorial ChromaDB, chaves dos modelos de LLM
e autenticação JWT compartilhada com a API-DB.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # Conexão com a API do banco relacional
    DB_API_URL: str = "http://localhost:8000"
    API_DB_AUTH_TOKEN: Optional[str] = None

    # Autenticação JWT (mesma chave e algoritmo da api_db para validação cruzada)
    SECRET_KEY: str = "sua_chave_secreta_super_segura_metamorphia_2026"
    ALGORITHM: str = "HS256"

    # Segurança: CORS e Rate Limiting
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
    RATE_LIMIT_DEFAULT: str = "120/minute"

    # Persistência do banco vetorial ChromaDB
    CHROMA_PATH: str = str(_BASE_DIR / "data" / "chroma")
    CHROMA_COLLECTION: str = "metamorphia_memoria"
    CHROMA_COLLECTION_MEMORIA: str = "metamorphia_memoria"
    CHROMA_COLLECTION_DOCS: str = "metamorphia_documentos"
    RAW_DATA_PATH: str = str(_BASE_DIR / "data" / "raw")

    # Provedores de Inteligência Artificial
    LLM_PROVIDER: str = "gemini"  # gemini | groq | auto | mock
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    GEMINI_MAX_RPM: int = 15
    GEMINI_MAX_RPD: int = 500
    AI_ALLOWED_EMAILS: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_BUCKET: str = "metamorphia-arquivos"

    model_config = SettingsConfigDict(
        env_file=str(_BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância singleton de configurações da aplicação."""
    cfg = Settings()
    if cfg.ENVIRONMENT.strip().lower() == "production" and (
        cfg.SECRET_KEY == "sua_chave_secreta_super_segura_metamorphia_2026"
        or len(cfg.SECRET_KEY.strip()) < 32
    ):
        raise RuntimeError("SECRET_KEY insegura ou padrão não permitida em produção.")
    return cfg
