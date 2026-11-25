# MetamorphIA_AI-API (`API_IA`)

Serviço FastAPI de Inteligência Artificial para apoio pedagógico inclusivo, adaptação de atividades curriculares e elaboração de PEIs (Resolução SEDUC nº 129/2025).

## Arquitetura de Dados e Segurança
- **ChromaDB (`metamorphia_documentos`)**: utilizado exclusivamente para busca semântica nos PDFs de referência (DUA, Decreto 12.686, WWC). Nenhum dado de aluno é armazenado no ChromaDB.
- **API_DB + Supabase Storage**: toda memória consolidada (`perfil_ia`), histórico de conversas (`chat`/`mensagem`), validade bimestral do PEI (`pei_feito`/`pei_valido_ate` de 60 dias) e arquivos `.docx` de PEI vivem exclusivamente na `API_DB` e no Supabase Storage.
- **Autenticação JWT**: valida assinatura `HS256`, `iss`/`aud`, `purpose=access`, `is_verified=True` e revalida sessão ativa (`token_version`, existência e permissões) junto à `API_DB` (`/autenticar/me`).

## Variáveis de Ambiente
- `ENVIRONMENT`: `development` | `test` | `production` (em produção, desabilita Swagger/ReDoc e rotas `/mock/*`).
- `ENABLE_MOCK_ROUTES`: `false` por padrão; só habilita `/mock/*` se `true` fora de produção.
- `SECRET_KEY`: **obrigatória** (mínimo 32 caracteres), idêntica à `API_DB`.
- `DB_API_URL`: URL base da `API_DB` (ex.: `http://localhost:8000`).
- `LLM_PROVIDER`: `gemini` | `groq` | `auto` | `mock`.
- `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_MAX_RPM`, `GEMINI_MAX_RPD`.
- `GROQ_API_KEY`, `GROQ_MODEL`.
- `CORS_ORIGINS`: origens separadas por vírgula.
- `RATE_LIMIT_DEFAULT`: limite padrão (ex.: `120/minute`).
- `AI_ALLOWED_EMAILS`: lista opcional separada por vírgula de e-mails autorizados.
- `SUPABASE_URL`, `SUPABASE_ASSETS_KEY` (ou `SUPABASE_SERVICE_KEY`), `SUPABASE_BUCKET`: restauração de `chroma.zip` e `pei_estilo.zip` no startup.

## Execução Local e Testes
```bash
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
pytest -v
```

## Deploy (Render)
Configurado em `render.yaml` (`uvicorn main:app --host 0.0.0.0 --port $PORT`, health check em `/health`).
Preencher manualmente no painel do Render todas as variáveis marcadas com `sync: false`.
