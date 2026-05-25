#Esse cara vai ler os arquivos da pasta data/raw e faz os chunks das mesmas
from langchain_community.document_loaders import PyPDFLoader
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
raw_path = os.path.join(BASE_DIR, "data", "raw")
chunks=[]
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
for filename in os.listdir(raw_path):
    if filename.endswith(".pdf"):
        pdf_path = os.path.join(raw_path, filename)
        loader = PyPDFLoader(pdf_path)
        docs = loader.load()
        chunks.extend(text_splitter.split_documents(docs))

print(chunks[2].metadata)