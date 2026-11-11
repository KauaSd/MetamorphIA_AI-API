"""Persistência no ChromaDB e geração de embeddings pelo Gemini."""

import os
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Deque, Sequence

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
CHROMA_PATH = Path(os.getenv("CHROMA_PERSIST_DIRECTORY", BASE_DIR / "data" / "chroma"))
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "metamorphia")
EMBEDDING_MODEL = "gemini-embedding-2-preview"

# Limites da chave.
MAX_REQUESTS_PER_MINUTE = 100
MAX_TOKENS_PER_MINUTE = 30_000
MAX_REQUESTS_PER_DAY = 1_000
MAX_CONTENTS_PER_REQUEST = 90
MINUTE = 60
DAY = 86_400


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    """Lê e valida uma configuração numérica do ambiente."""
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        comparison = "maior que zero" if minimum == 1 else f"maior ou igual a {minimum}"
        raise ValueError(f"{name} deve ser {comparison}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} não pode ser maior que {maximum}")
    return value


EMBEDDING_BATCH_SIZE = _env_int("GEMINI_EMBEDDING_BATCH_SIZE", 50)
EMBEDDING_MAX_RETRIES = _env_int("GEMINI_EMBEDDING_MAX_RETRIES", 5, minimum=0)
CHROMA_UPSERT_BATCH_SIZE = _env_int("CHROMA_UPSERT_BATCH_SIZE", 50)
REQUESTS_PER_MINUTE = _env_int(
    "GEMINI_EMBEDDING_RPM", MAX_REQUESTS_PER_MINUTE, maximum=MAX_REQUESTS_PER_MINUTE
)
TOKENS_PER_MINUTE = _env_int(
    "GEMINI_EMBEDDING_TPM", MAX_TOKENS_PER_MINUTE, maximum=MAX_TOKENS_PER_MINUTE
)
REQUESTS_PER_DAY = _env_int(
    "GEMINI_EMBEDDING_RPD", MAX_REQUESTS_PER_DAY, maximum=MAX_REQUESTS_PER_DAY
)


class GeminiRateLimiter:
    """Controla as cotas do Gemini usando janelas móveis."""

    def __init__(
        self,
        requests_per_minute: int = REQUESTS_PER_MINUTE,
        tokens_per_minute: int = TOKENS_PER_MINUTE,
        requests_per_day: int = REQUESTS_PER_DAY,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_day = requests_per_day
        self._minute_requests: Deque[float] = deque()
        self._minute_tokens: Deque[tuple[float, int]] = deque()
        self._day_requests: Deque[float] = deque()
        self._lock = Lock()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estima tokens pelo número de bytes UTF-8, sem subestimar Unicode."""
        return max(1, len(text.encode("utf-8")))

    def _discard_expired(self, now: float) -> None:
        while self._minute_requests and now - self._minute_requests[0] >= MINUTE:
            self._minute_requests.popleft()
        while self._minute_tokens and now - self._minute_tokens[0][0] >= MINUTE:
            self._minute_tokens.popleft()
        while self._day_requests and now - self._day_requests[0] >= DAY:
            self._day_requests.popleft()

    def _wait_times(self, now: float, tokens: int) -> list[float]:
        waits = []
        if len(self._minute_requests) >= self.requests_per_minute:
            waits.append(self._minute_requests[0] + MINUTE - now)
        if sum(amount for _, amount in self._minute_tokens) + tokens > self.tokens_per_minute:
            waits.append(self._minute_tokens[0][0] + MINUTE - now)
        if len(self._day_requests) >= self.requests_per_day:
            waits.append(self._day_requests[0] + DAY - now)
        return waits

    def acquire(self, tokens: int) -> None:
        """Espera capacidade e reserva uma chamada para ``tokens`` estimados."""
        if tokens > self.tokens_per_minute:
            raise ValueError("Um único lote excede o limite de tokens por minuto; reduza o chunk.")

        while True:
            with self._lock:
                now = time.monotonic()
                self._discard_expired(now)
                waits = self._wait_times(now, tokens)
                if not waits:
                    self._minute_requests.append(now)
                    self._minute_tokens.append((now, tokens))
                    self._day_requests.append(now)
                    return
            time.sleep(max(0.01, max(waits)))


# Compartilhado por todo o processo para que novas coleções não reiniciem a cota.
GEMINI_RATE_LIMITER = GeminiRateLimiter()


class GeminiEmbeddingFunction(EmbeddingFunction[Documents]):
    """Gera embeddings de documentos e consultas no formato esperado pelo Chroma."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = EMBEDDING_MODEL
        self.batch_size = EMBEDDING_BATCH_SIZE
        self.max_retries = EMBEDDING_MAX_RETRIES
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        """Inicializa o cliente apenas quando ele for usado."""
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    @staticmethod
    def _is_transient_error(error: Exception) -> bool:
        message = str(error).lower()
        retryable_statuses = ("429", "500", "502", "503", "504")
        retryable_reasons = (
            "resource_exhausted", "rate limit", "unavailable", "deadline exceeded",
            "connecterror", "temporary failure", "name resolution",
        )
        return any(value in message for value in retryable_statuses + retryable_reasons)

    def _batches_within_token_limit(self, documents: Sequence[str]) -> list[list[str]]:
        """Divide textos pelos limites de itens e tokens de uma chamada."""
        batches: list[list[str]] = []
        batch: list[str] = []
        batch_tokens = 0

        for document in documents:
            tokens = GEMINI_RATE_LIMITER.estimate_tokens(document)
            if tokens > GEMINI_RATE_LIMITER.tokens_per_minute:
                raise ValueError("Um chunk excede o limite de tokens estimados; reduza o tamanho dos chunks.")
            is_full = (
                len(batch) >= self.batch_size
                or batch_tokens + tokens > GEMINI_RATE_LIMITER.tokens_per_minute
            )
            if batch and is_full:
                batches.append(batch)
                batch, batch_tokens = [], 0
            batch.append(document)
            batch_tokens += tokens

        if batch:
            batches.append(batch)
        return batches

    def _request_embeddings(self, documents: Sequence[str], task_type: str) -> Embeddings:
        tokens = sum(
            GEMINI_RATE_LIMITER.estimate_tokens(document) for document in documents
        )
        contents = [types.Content(parts=[types.Part(text=document)]) for document in documents]

        for attempt in range(self.max_retries + 1):
            # Também reserva as tentativas que falham, pois elas contam na cota.
            GEMINI_RATE_LIMITER.acquire(tokens)
            try:
                response = self.client.models.embed_content(
                    model=self.model,
                    contents=contents,
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                return [embedding.values for embedding in response.embeddings or []]
            except Exception as error:
                if attempt == self.max_retries or not self._is_transient_error(error):
                    raise
                time.sleep(2**attempt)

        raise RuntimeError("Não foi possível gerar embeddings.")

    def _embed(self, input: Documents, task_type: str) -> Embeddings:
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY não foi configurada. Adicione-a ao arquivo .env "
                "para usar o banco vetorial."
            )

        documents = list(input)
        embeddings = []
        for batch in self._batches_within_token_limit(documents):
            for start in range(0, len(batch), MAX_CONTENTS_PER_REQUEST):
                sub_batch = batch[start : start + MAX_CONTENTS_PER_REQUEST]
                embeddings.extend(self._request_embeddings(sub_batch, task_type))
        if len(embeddings) != len(documents):
            raise ValueError(
                "O Gemini retornou uma quantidade de embeddings diferente da "
                f"quantidade de documentos: {len(embeddings)} para {len(documents)}."
            )
        return embeddings

    def __call__(self, input: Documents) -> Embeddings:
        return self._embed(input, "RETRIEVAL_DOCUMENT")

    def embed_query(self, input: Documents) -> Embeddings:
        return self._embed(input, "RETRIEVAL_QUERY")

    @staticmethod
    def name() -> str:
        return "gemini_embedding"

    def get_config(self) -> dict[str, str]:
        return {"model": self.model}

    @staticmethod
    def build_from_config(config: dict[str, str]) -> "GeminiEmbeddingFunction":
        return GeminiEmbeddingFunction()


def get_client() -> chromadb.PersistentClient:
    """Abre o banco persistente configurado para a aplicação."""
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


def get_collection(create: bool = True):
    """Obtém a coleção configurada, criando-a quando necessário."""
    client = get_client()
    embedding_function = GeminiEmbeddingFunction()
    if create:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(name=COLLECTION_NAME, embedding_function=embedding_function)


def adicionar_documentos(
    documentos: Sequence[str],
    ids: Sequence[str],
    metadatas: Sequence[dict[str, str]] | None = None,
) -> None:
    """Insere ou atualiza documentos, em lotes seguros para o Gemini."""
    if not documentos:
        return
    if len(documentos) != len(ids):
        raise ValueError("documentos e ids devem ter a mesma quantidade de itens")
    if metadatas is not None and len(metadatas) != len(documentos):
        raise ValueError("metadatas e documentos devem ter a mesma quantidade de itens")

    collection = get_collection()
    for start in range(0, len(documentos), CHROMA_UPSERT_BATCH_SIZE):
        end = start + CHROMA_UPSERT_BATCH_SIZE
        collection.upsert(
            documents=list(documentos[start:end]),
            ids=list(ids[start:end]),
            metadatas=list(metadatas[start:end]) if metadatas is not None else None,
        )
