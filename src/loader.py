"""Leitura e divisão dos PDFs que serão indexados."""

from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_PATH = BASE_DIR / "data" / "raw"


def carregar_pdfs(
    pasta_raw: Path | str = RAW_PATH,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[Document]:
    """Lê todos os PDFs de ``pasta_raw`` e retorna seus chunks.

    Os arquivos são processados em ordem alfabética para tornar a ingestão
    reproduzível. O caminho absoluto do PDF fica salvo em ``metadata['source']``.
    """
    pasta_raw = Path(pasta_raw)
    if not pasta_raw.exists():
        raise FileNotFoundError(f"Pasta de PDFs não encontrada: {pasta_raw}")
    if not pasta_raw.is_dir():
        raise NotADirectoryError(f"O caminho informado não é uma pasta: {pasta_raw}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks: list[Document] = []

    for pdf_path in sorted(
        (
            arquivo
            for arquivo in pasta_raw.iterdir()
            if arquivo.is_file() and arquivo.suffix.lower() == ".pdf"
        ),
        key=lambda arquivo: arquivo.name.lower(),
    ):
        documentos = PyPDFLoader(str(pdf_path)).load()
        for documento in documentos:
            documento.metadata["source"] = str(pdf_path.resolve())
        chunks.extend(splitter.split_documents(documentos))

    return chunks
