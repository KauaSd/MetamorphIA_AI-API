"""Configurações centralizadas da aplicação API-IA MetamorphIA.

Carrega variáveis de ambiente via dotenv e exige SECRET_KEY forte para
validação cruzada de JWT com a API-DB.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import secrets
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")

_CHAVE_PADRAO_INSEGURA = "sua_chave_secreta_super_segura_metamorphia_2026"
_CHAVE_EFEMERA_TESTE = secrets.token_hex(32)


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    ENABLE_MOCK_ROUTES: bool = False

    # Conexão com a API do banco relacional
    DB_API_URL: str = "http://localhost:8000"

    # Autenticação JWT (mesma chave, algoritmo, issuer e audience da api_db)
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "metamorphia-db-api"
    JWT_AUDIENCE: str = "metamorphia-services"

    # Segurança: CORS e Rate Limiting
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
    RATE_LIMIT_DEFAULT: str = "120/minute"

    # Persistência do banco vetorial ChromaDB (somente documentos de referência)
    CHROMA_PATH: str = str(_BASE_DIR / "data" / "chroma")
    CHROMA_COLLECTION: str = "metamorphia_documentos"
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
    JWT_SESSION_CACHE_TTL_SECONDS: int = 120
    SUPABASE_URL: str = ""
    SUPABASE_ASSETS_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_BUCKET: str = "metamorphia-arquivos"

    model_config = SettingsConfigDict(
        env_file=str(_BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância singleton de configurações da aplicação validando SECRET_KEY."""
    cfg = Settings()
    env_norm = cfg.ENVIRONMENT.strip().lower()
    chave = (cfg.SECRET_KEY or "").strip()

    if env_norm in ("test", "testing") and (not chave or chave == _CHAVE_PADRAO_INSEGURA):
        cfg.SECRET_KEY = _CHAVE_EFEMERA_TESTE
        return cfg

    if not chave or chave == _CHAVE_PADRAO_INSEGURA or len(chave) < 32:
        raise RuntimeError(
            "SECRET_KEY ausente, padrão ou com menos de 32 caracteres. "
            "Configure uma SECRET_KEY forte no ambiente."
        )
    return cfg
