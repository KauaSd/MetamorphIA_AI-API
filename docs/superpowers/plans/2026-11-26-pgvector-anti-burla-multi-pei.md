# Plano de Implementação: Memória Vetorial por Aluno (`pgvector`), Blindagem Anti-Burla/Anti-Alucinação, Controle Multi-PEI, Modo Teste de Dupla IA e Aceleração Full-Stack

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir a memória textual única por uma base vetorial por aluno em `pgvector` alimentada pelos PEIs por disciplina e vivências do chat, blindar a IA contra pedidos de código (*promptscript*) e respostas prontas (entregando apenas estratégias de adaptação), exigir a conclusão de 100% dos PEIs das disciplinas do professor antes de liberar o chat, disponibilizar um Modo Teste com 1ª IA Analisadora (%) e acelerar toda a comunicação Frontend ↔ API-DB ↔ API-IA removendo `localStorage` indevido.

**Architecture:** A `api_db` passa a hospedar a tabela `aluno_memoria_vetorial` (`Vector(384)` no PostgreSQL via `pgvector`, com fallback JSON+cosseno em SQLite de teste) e endpoints de upsert/busca vetorial e cálculo de `todos_peis_concluidos` por professor. A `Ia_metamorphia` gera embeddings locais de 384 dimensões (`DefaultEmbeddingFunction` ONNX / fallback determinístico), aplica barreira determinística anti-código/anti-gabarito ciente do nome do aluno, cruza o input com o `pgvector`, opcionalmente passa pela 1ª IA Analisadora no Modo Teste e executa persistência/indexação de vivências em `BackgroundTasks`. O frontend (`MetamorphIA_Frontend-2`) remove `localStorage` de professores e perfil, consome `todos_concluidos`/`disciplinas_pendentes` e elimina cascatas HTTP no chat e CRUD.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL + `pgvector>=0.3.0`, `chromadb` (ONNX `all-MiniLM-L6-v2`), `httpx`, `pytest`, Next.js 16, React 19, TypeScript, `@tanstack/react-query`.

**Spec:** [2026-11-26-pgvector-anti-burla-multi-pei-design.md](file:///C:/Users/kauas/OneDrive/Documentos/Projetos/Ia_metamorphia/docs/superpowers/specs/2026-11-26-pgvector-anti-burla-multi-pei-design.md)

## Global Constraints

- **NUNCA executar `git commit`** na árvore principal dos repositórios.
- **Cota Gemini (`GEMINI_MAX_RPM=15`):** geração de embeddings para `pgvector` deve rodar localmente (0 chamadas Gemini); a 2ª IA (Analisadora) só roda quando `modo_teste_analisador=True` ou `ENABLE_ANALYZER_AI=true`.
- **Compatibilidade PostgreSQL + SQLite:** PostgreSQL usa `pgvector` (`Vector(384)` + `<=>`); SQLite de testes usa JSON serializado + similaridade cosseno em Python.
- **LGPD:** todo texto indexado em `aluno_memoria_vetorial` deve passar por `sanitizar_dados_sensiveis`.
- **Zero respostas prontas / zero código:** a IA nunca retorna código-fonte nem resolve exercícios; entrega apenas estratégias de adaptação pedagógica.

## Review Focus

- **Promptscript disfarçado com dados do aluno:** ex.: `"Meu aluno Lucas (TEA) queria um codigo em python para gerar respostas se e ou nao e um numero par..."` — deve ser barrado antes da IA principal mesmo citando nome do aluno, `"aluno"` e `"TEA"`.
- **Professor com múltiplas disciplinas:** se o professor tem `["Matemática", "Física"]` e só concluiu Matemática, `todos_peis_concluidos` deve ser `False`, `disciplinas_pendentes` deve conter `["Física"]` e o chat deve retornar HTTP 403 tanto na `api_db` quanto na `Ia_metamorphia` e permanecer bloqueado no frontend.
- **Múltiplos PEIs no `pgvector`:** ao criar/importar PEI de `Física` depois de `Matemática`, os chunks de `Matemática` devem permanecer intactos na tabela vetorial e acessíveis em perguntas sobre Matemática.
- **Nome próprio do aluno sem a palavra "aluno":** ex.: `"O Lucas ficou agitado hoje na troca de atividade"` deve passar nos guardrails usando o `nome_aluno` do contexto.
- **Exclusão de conversa ou aluno:** apagar uma conversa deve remover também os vetores `origem="relato_chat"` daquela `conversa_id`; apagar o aluno deve remover todos os seus vetores.

---

### Task 1: Tabela `aluno_memoria_vetorial` (`pgvector`) e Endpoints Vetoriais na `api_db`

**Files:**
- Modify: `api_db/MetamorphIA_DB-API/requirements.txt`
- Modify: `api_db/MetamorphIA_DB-API/.bd/BD_MetamorphIA.sql`
- Modify: `api_db/MetamorphIA_DB-API/app/database.py`
- Modify: `api_db/MetamorphIA_DB-API/app/routers/students.py`
- Modify: `api_db/MetamorphIA_DB-API/app/services/students.py`
- Test: `tests/test_pgvector_memoria_aluno.py`

**Interfaces:**
- Produces na `api_db`:
  - Modelo ORM `AlunoMemoriaVetorial` em `database.py`
  - `POST /Alunos/{id_aluno}/vetores` (`SalvarVetoresAlunoRequest`)
  - `POST /Alunos/{id_aluno}/vetores/busca` (`BuscarVetoresAlunoRequest` -> `{"trechos": [...], "disciplinas_pei": [...]}`)

- [ ] **Step 1: Escrever o teste falhando para persistência e busca vetorial por aluno**

Criar `tests/test_pgvector_memoria_aluno.py` testando via `apps_e_clientes` (ou app FastAPI `api_db`):
- `POST /Alunos/{id_aluno}/vetores` salvando chunks de PEI de `"Matemática"` e `"Física"` e um `"relato_chat"` com `conversa_id="conv-1"`.
- Reenviar `POST /Alunos/{id_aluno}/vetores` com `substituir_origem_disciplina=True` para `"Matemática"` e verificar que os chunks de `"Física"` e `"relato_chat"` continuam lá.
- `POST /Alunos/{id_aluno}/vetores/busca` retornando os chunks ordenados por `similaridade` e filtrando/identificando `disciplinas_pei`.
- `DELETE /Alunos/{id_aluno}/conversas-db/conv-1` removendo apenas o vetor de `conversa_id="conv-1"`.

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_pgvector_memoria_aluno.py -v`
Expected: FAIL (404 / modelo `AlunoMemoriaVetorial` inexistente).

- [ ] **Step 3: Adicionar `pgvector>=0.3.0` e definir `AlunoMemoriaVetorial` + DDL**

1. Em `api_db/MetamorphIA_DB-API/requirements.txt`, adicionar `pgvector>=0.3.0`.
2. Em `api_db/MetamorphIA_DB-API/app/database.py`:
   - Tentar importar `Vector` de `pgvector.sqlalchemy` (com fallback seguro caso `pgvector` não esteja instalado em algum ambiente).
   - Quando `not _is_sqlite` e `Vector` disponível, usar `Column(Vector(384), nullable=False)`; quando `_is_sqlite`, usar `Column(Text, nullable=False)` armazenando JSON.
   - Em `_inicializar_banco_com_fallback()` / `_garantir_colunas_novas(eng)`: se `eng.dialect.name == "postgresql"`, executar `CREATE EXTENSION IF NOT EXISTS vector;` antes de `create_all`, e criar índice HNSW se não existir.
3. Atualizar `api_db/MetamorphIA_DB-API/.bd/BD_MetamorphIA.sql` com `CREATE EXTENSION IF NOT EXISTS vector;` e `CREATE TABLE aluno_memoria_vetorial (...)`.

- [ ] **Step 4: Implementar endpoints vetoriais e limpeza em `students.py` e `services/students.py`**

1. Em `api_db/MetamorphIA_DB-API/app/routers/students.py`:
   - `POST /Alunos/{id_aluno}/vetores`: valida acesso ao aluno, deduplica por `hash_chunk`, se `substituir_origem_disciplina=True` remove chunks existentes de mesmo `(id_aluno, origem, disciplina_normalizada)`, insere novos vetores.
   - `POST /Alunos/{id_aluno}/vetores/busca`: no PostgreSQL usa operador `<=>` do `pgvector` ordenado por menor distância; no SQLite calcula similaridade cosseno em Python. Retorna `trechos` (com `origem`, `disciplina`, `texto`, `similaridade`) e `disciplinas_pei` (lista distinta de disciplinas presentes com `origem='pei'`).
   - Em `DeletarConversaAlunoDB`: deletar linhas de `AlunoMemoriaVetorial` onde `conversa_id == conversa_id` e `id_aluno == id_aluno`.
2. Em `api_db/MetamorphIA_DB-API/app/services/students.py` (`DeletaAluno`): incluir `delete(AlunoMemoriaVetorial).where(AlunoMemoriaVetorial.id_aluno == id_aluno)`.

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `pytest tests/test_pgvector_memoria_aluno.py -v`
Expected: PASS.

---

### Task 2: Controle Multi-PEI por Disciplina na `api_db`

**Files:**
- Modify: `api_db/MetamorphIA_DB-API/app/services/disciplinas.py`
- Modify: `api_db/MetamorphIA_DB-API/app/routers/students.py`
- Modify: `api_db/MetamorphIA_DB-API/app/routers/ai.py`
- Test: `tests/test_multi_pei_liberacao.py`

**Interfaces:**
- Produces em `app/services/disciplinas.py`:
  - `avaliar_conclusao_peis_professor(sessao: Session, aluno: Aluno, professor: Usuario) -> dict[str, Any]` retornando `{"todos_peis_concluidos": bool, "disciplinas_exigidas": list[str], "disciplinas_concluidas": list[str], "disciplinas_pendentes": list[str]}`.
- Updates em `GET /Alunos/{id_aluno}` e `_exigir_pei_valido_para_chat` em `routers/ai.py`.

- [ ] **Step 1: Escrever o teste falhando para bloqueio Multi-PEI por disciplina**

Criar `tests/test_multi_pei_liberacao.py`:
- Professor com tags `["Matemática", "Física"]`:
  - Com 0 PEIs: `GET /Alunos/{id}` retorna `todos_peis_concluidos=False`, `disciplinas_pendentes=["Matemática", "Física"]`; `POST /AI/chat/aluno/{id}` retorna `403`.
  - Após concluir PEI de `"Matemática"`: `GET /Alunos/{id}` ainda retorna `todos_peis_concluidos=False`, `disciplinas_pendentes=["Física"]`; `POST /AI/chat/aluno/{id}` ainda retorna `403` citando `"Física"`.
  - Após concluir PEI de `"Física"`: `GET /Alunos/{id}` retorna `todos_peis_concluidos=True`, `disciplinas_pendentes=[]`; `POST /AI/chat/aluno/{id}` retorna `200`.

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_multi_pei_liberacao.py -v`
Expected: FAIL (chat liberado após o 1º PEI).

- [ ] **Step 3: Implementar `avaliar_conclusao_peis_professor` e aplicar na `api_db`**

1. Em `api_db/MetamorphIA_DB-API/app/services/disciplinas.py`:
   - Implementar `avaliar_conclusao_peis_professor(sessao, aluno, professor)`:
     - Resolve as tags da escola do aluno (`User_esc.tags_areas`) ou `professor.tags_usuario` via `_normalizar_tags` / JSON.
     - Compara com `PeiDisciplina` onde `id_aluno == aluno.id_aluno` e `status == "concluido"`.
     - Se `not pei_esta_valido(aluno)`, todas as exigidas ficam pendentes.
     - Se o professor não tem tags cadastradas, exige `pei_esta_valido(aluno)`.
2. Em `api_db/MetamorphIA_DB-API/app/routers/students.py` (`ObterAlunoPorId`): incluir `todos_peis_concluidos`, `disciplinas_concluidas`, `disciplinas_pendentes`.
3. Em `api_db/MetamorphIA_DB-API/app/routers/ai.py`:
   - Atualizar `_exigir_pei_valido_para_chat(aluno_obj, sessao=None, professor=None)` usando `avaliar_conclusao_peis_professor` quando `sessao` e `professor` forem passados (retrocompatível) e bloquear com `403` listando as disciplinas pendentes.
   - Em `deletar_conversa_aluno_ia`: garantir exclusão dos vetores daquela conversa se ainda existirem.

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `pytest tests/test_multi_pei_liberacao.py -v`
Expected: PASS.

---

### Task 3: Embeddings Locais (`384d`) e Cliente Vetorial na `Ia_metamorphia`

**Files:**
- Create: `memoria/student_vectors.py`
- Modify: `db_client.py`
- Modify: `schemas.py`
- Test: `tests/test_student_vectors_unit.py`

**Interfaces:**
- Produces em `memoria/student_vectors.py`:
  - `gerar_embedding_local(texto: str) -> list[float]` (sempre 384 floats normalizados)
  - `dividir_texto_pei_em_chunks(texto_pei: str, disciplina: str, nome_aluno: str) -> list[dict[str, str]]`
  - `indexar_contexto_aluno_pgvector(id_aluno: int, origem: str, textos: list[str], disciplina: str | None = None, pei_id: str | None = None, conversa_id: str | None = None, substituir_origem_disciplina: bool = False, token: str | None = None) -> bool`
  - `buscar_contexto_vetorial_aluno(id_aluno: int, query: str, top_k: int = 6, disciplina: str | None = None, token: str | None = None) -> dict[str, Any]`
- Produces em `db_client.py`:
  - `salvar_vetores_aluno_db(...)` e `buscar_vetores_aluno_db(...)`
  - Todos os métodos de `db_client.py` usando `obter_cliente_http()` (pool keep-alive).

- [ ] **Step 1: Escrever teste unitário para embeddings e chunking de PEI**

Criar `tests/test_student_vectors_unit.py` validando:
- `gerar_embedding_local("texto teste")` retorna lista de 384 floats com norma ~1.0.
- `dividir_texto_pei_em_chunks(...)` gera chunks higienizados LGPD com prefixo de disciplina.
- `indexar_contexto_aluno_pgvector` e `buscar_contexto_vetorial_aluno` chamam `db_client` corretamente.

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_student_vectors_unit.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'memoria.student_vectors'`).

- [ ] **Step 3: Implementar `memoria/student_vectors.py`, atualizar `db_client.py` e `schemas.py`**

1. Criar `memoria/student_vectors.py` usando `DefaultEmbeddingFunction` com fallback hash-384 determinístico (e sanitização via `sanitizar_dados_sensiveis`).
2. Em `db_client.py`:
   - Trocar `async with httpx.AsyncClient(...)` avulsos por `async with obter_cliente_http(timeout) as client:` (reuso de conexões TCP/TLS).
   - Adicionar `salvar_vetores_aluno_db` e `buscar_vetores_aluno_db`.
3. Em `schemas.py`:
   - Adicionar em `AlunoDB`: `todos_peis_concluidos: Optional[bool] = None`, `disciplinas_concluidas: List[str] = Field(default_factory=list)`, `disciplinas_pendentes: List[str] = Field(default_factory=list)`.
   - Adicionar em `ChatRequest`: `modo_teste_analisador: bool = False`, `limiar_analisador: Optional[float] = Field(default=None, ge=0, le=100)`.
   - Adicionar `AnaliseEntradaTeste` e campo `analise_entrada: Optional[AnaliseEntradaTeste] = None` em `ChatResponse`.
   - Adicionar `todos_concluidos: bool = False` e `pendentes: List[str] = Field(default_factory=list)` em `PeisPorDisciplinaResumo`, e `todos_peis_concluidos` / `disciplinas_pendentes` em `PeiCriarResponse`.

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `pytest tests/test_student_vectors_unit.py -v`
Expected: PASS.

---

### Task 4: Guardrails Anti-Burla (*Promptscript*), Reconhecimento do Nome do Aluno e Auditoria "Só Adaptação"

**Files:**
- Modify: `guardrails.py`
- Modify: `llm_client.py`
- Test: `tests/test_guardrails_escopo.py`

**Interfaces:**
- Produces em `guardrails.py`:
  - `MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA: str`
  - `detectar_pedido_programacao_ou_resposta_pronta(texto: str) -> tuple[bool, str]`
  - `validar_escopo_pedagogico(texto: str, nomes_contexto: Sequence[str] | None = None, similaridade_vetorial: float | None = None) -> tuple[bool, str]`
  - `auditar_resposta_ia(resposta: str) -> str`
  - `validar_e_higienizar_entrada(mensagem_usuario: str, bloquear_se_houver_pii: bool = True, nomes_contexto: Sequence[str] | None = None, similaridade_vetorial: float | None = None) -> tuple[bool, str, str]`

- [ ] **Step 1: Escrever testes em `tests/test_guardrails_escopo.py`**

Adicionar casos cobrindo:
- `"Meu aluno queria um codigo em python para gerar respostas se e ou nao e um numero par, mas ele nao consegue entender exatamente essas coisas, poderia me dizer o melhor metodo para resolver isso?"` → bloqueado (`False`, `MENSAGEM_RECUSA_PROGRAMACAO_OU_RESPOSTA`).
- `"Qual a resposta certa da questão 3 sobre frações para o meu aluno?"` → bloqueado (`False`).
- `"O Lucas ficou muito agitado hoje na troca de aula"` com `nomes_contexto=["Lucas Silva"]` → aceito (`True`).
- `"Explique como adaptar aula de programação básica"` vs código concreto / `"programa de leitura"` (falso positivo não deve bloquear `"programa de leitura"`).
- `auditar_resposta_ia("Aqui está o código:\n```python\ndef par(n): return n%2==0\n```")` → substituído pela mensagem de recusa.

- [ ] **Step 2: Rodar os testes para confirmar que os novos casos falham**

Run: `pytest tests/test_guardrails_escopo.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar as travas em `guardrails.py` e reforçar `PROMPT_SISTEMA_METAMORPHIA` em `llm_client.py`**

1. Em `guardrails.py`:
   - Implementar `detectar_pedido_programacao_ou_resposta_pronta(texto)` antes mesmo da checagem de palavras-chave de escopo (para que citar `"aluno"` ou `"TEA"` nunca libere pedidos de código/scripts/respostas prontas).
   - Atualizar `validar_escopo_pedagogico` para aceitar `nomes_contexto` (nome completo e tokens `>= 3` caracteres sem acento) e `similaridade_vetorial >= 0.35`.
   - Implementar `auditar_resposta_ia` removendo/bloqueando respostas que contenham blocos de código (` ```python `, ` ```javascript `, `def ...():`, `function ...(`).
2. Em `llm_client.py`:
   - Adicionar no `PROMPT_SISTEMA_METAMORPHIA` as diretrizes pétreas: **NUNCA dar respostas prontas de exercícios/contas/redações nem código de programação** (entregar apenas estratégias de adaptação pedagógica) e **NUNCA alucinar fatos que contradigam os vetores/PEIs do aluno**.
   - Chamar `auditar_resposta_ia` no retorno de `gerar_resposta`.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_guardrails_escopo.py -v`
Expected: PASS.

---

### Task 5: IA Analisadora (Modo Teste %), Fluxo `pgvector` + `BackgroundTasks` no `chat.py`, `pei.py` e `dashboard_aluno.py`

**Files:**
- Create: `verificador_pertinencia.py`
- Modify: `config.py`
- Modify: `api/routes/chat.py`
- Modify: `api/routes/pei.py`
- Modify: `api/routes/dashboard_aluno.py`
- Modify: `api/routes/query.py`
- Modify: `api_db/MetamorphIA_DB-API/app/routers/ai.py`
- Test: `tests/test_analisador_modo_teste.py`

**Interfaces:**
- Produces em `verificador_pertinencia.py`:
  - `ResultadoAnaliseEntrada` (dataclass/Pydantic com `porcentagem`, `limiar`, `aprovado`, `motivo`, `relacionado_ao_aluno_ou_pei`, `pede_resposta_pronta_ou_codigo`, `disciplina_identificada`, `latencia_ms`)
  - `analisar_entrada_com_ia(mensagem: str, nome_aluno: str, neurodivergencia: str, trechos_vetoriais: list[dict], disciplinas_pei: list[str], limiar: float = 70.0) -> ResultadoAnaliseEntrada`
- Produces em `api/routes/chat.py`:
  - `POST /alunos/{id_aluno}/chat` usando: `_exigir_pei_valido_aluno` (multi-PEI), `asyncio.gather` para buscas, `pgvector`, `analisar_entrada_com_ia` (quando `modo_teste_analisador=True` ou `settings.ENABLE_ANALYZER_AI`), e `BackgroundTasks` para indexar vivência no `pgvector` + atualizar síntese sem bloquear a resposta.
  - `POST /alunos/{id_aluno}/chat/testar-analisador` (também proxiado em `api_db` `/AI/chat/aluno/{id_aluno}/testar-analisador`) para testar e comparar `%` + chunks `pgvector` + resposta opcional da 2ª IA.

- [ ] **Step 1: Escrever teste para Modo Teste da IA Analisadora, indexação de PEI no `pgvector` e `BackgroundTasks`**

Criar `tests/test_analisador_modo_teste.py`:
- Testar `POST /alunos/{id_aluno}/chat` com `modo_teste_analisador=True` e mock da 1ª IA retornando `porcentagem_pertinencia=20` → bloqueia sem chamar a 2ª IA e devolve `analise_entrada.aprovado is False`.
- Testar com `porcentagem_pertinencia=88` → chama a 2ª IA e devolve `analise_entrada.aprovado is True`.
- Testar `POST /alunos/{id_aluno}/chat/testar-analisador`.
- Testar que concluir/importar um PEI em `pei.py` indexa chunks no `pgvector` e retorna `todos_peis_concluidos` e `disciplinas_pendentes`.

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_analisador_modo_teste.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `verificador_pertinencia.py`, `config.py` e atualizar rotas**

1. Em `config.py`: adicionar `ENABLE_ANALYZER_AI: bool = False` e `ANALYZER_MIN_SCORE: float = 70.0`.
2. Criar `verificador_pertinencia.py` com prompt focado em JSON estrito (`temperature=0.0`) e fallback seguro em modo `mock`.
3. Em `api/routes/pei.py`:
   - Após criar, revisar ou importar PEI, indexar chunks por seção no `pgvector` (`substituir_origem_disciplina=True`, `disciplina=componente`) e calcular `todos_peis_concluidos` / `disciplinas_pendentes`.
4. Em `api/routes/chat.py`:
   - Atualizar `_exigir_pei_valido_aluno(aluno, usuario_autenticado)` exigindo 100% das disciplinas do professor concluídas e válidas.
   - Usar `asyncio.gather` para buscar vetores do aluno no `pgvector` + histórico + documentos de referência em paralelo.
   - Passar `nomes_contexto=[aluno.nome_aluno]` e `similaridade_vetorial` aos guardrails.
   - Executar 1ª IA Analisadora se `payload.modo_teste_analisador` ou `settings.ENABLE_ANALYZER_AI`.
   - Mover indexação do relato no `pgvector` e atualização de perfil/indicadores para `background_tasks.add_task(...)`.
   - Adicionar rota `POST /alunos/{id_aluno}/chat/testar-analisador`.
5. Em `api/routes/dashboard_aluno.py`: preencher `todos_concluidos` e `pendentes` em `PeisPorDisciplinaResumo`.
6. Em `api_db/MetamorphIA_DB-API/app/routers/ai.py`:
   - Criar cliente HTTP compartilhado (`_obter_cliente_ia_http()`) em vez de abrir `httpx.AsyncClient` por requisição.
   - Expor `POST /AI/chat/aluno/{id_aluno}/testar-analisador`.

- [ ] **Step 4: Rodar todos os testes do backend**

Run: `pytest -v`
Expected: ALL PASS (incluindo os testes existentes em `tests/`).

---

### Task 6: Frontend (`MetamorphIA_Frontend-2`) — Multi-PEI, Remoção de `localStorage` Indevido e Aceleração

**Files:**
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\utils\apiClient.ts`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\utils\data\types.ts`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\utils\data\apiRepository.ts`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\utils\data\controller.ts`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\utils\pei\service.ts`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\state\DataProvider.tsx`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\escolas\DashboardEscolaAdm.tsx`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\professores\ConteudoProfessores.tsx`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\alunos\ConteudoDashboardAluno.tsx`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\pei\Conteudopei.tsx`
- Modify: `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2\components\app\chat\ConteudoChat.tsx`

- [ ] **Step 1: Remover `localStorage` de perfil (`USER_PROFILE_KEY`) e professores (`metamorphia:adm:professores:*`)**

1. Em `utils/apiClient.ts`: manter `cachedUserProfile` em memória de módulo (com `getSavedUserProfile()` / `setSavedUserProfile()` em RAM e limpeza de eventual chave legada no `clearAuthSession`), sem gravar `metamorphia:auth:user` no `localStorage`.
2. Em `utils/data/apiRepository.ts`: adicionar `listarConvitesEscola(escolaId)` (`GET /escolas/{id}/convites`) e `removerProfessorEscola(escolaId, usuarioId)` (`DELETE /escolas/{id}/professores/{id_usuario}`).
3. Em `components/app/escolas/DashboardEscolaAdm.tsx` e `components/app/professores/ConteudoProfessores.tsx`: remover `STORAGE_KEY_PREFIX`, `lerProfessoresSalvos`, `lerProfessoresLocais` e `localStorage`; usar os dados reais da API + convites pendentes da API e invalidar `["escola-professores", escolaId]` / `["escola-peis-resumo", escolaId]`.

- [ ] **Step 2: Adaptar Frontend à regra Multi-PEI (bloquear chat até 100% das disciplinas concluídas)**

1. Em `utils/data/types.ts` e `utils/data/apiRepository.ts`: adicionar `todosConcluidos?: boolean` e `pendentes?: string[]` em `PeisPorDisciplinaResumo`.
2. Em `utils/pei/service.ts`: retornar `todosPeisConcluidos` e `disciplinasPendentes` em `PeiGeradoResultado`.
3. Em `components/app/alunos/ConteudoDashboardAluno.tsx`:
   - `chatLiberado` passa a exigir `todosConcluidos ?? (total > 0 ? concluidos >= total && !atrasado : peiGerado > 0)`.
4. Em `components/app/pei/Conteudopei.tsx` e `components/app/chat/ConteudoChat.tsx`:
   - Consultar `student-insights` / `disciplinasPendentes` para pré-selecionar a próxima disciplina pendente ao abrir criação de PEI.
   - Quando um PEI é concluído mas ainda há `disciplinasPendentes.length > 0`, informar claramente quais disciplinas faltam antes de liberar o chat do aluno.
   - Tratar `HTTP 403` no chat exibindo a mensagem clara vinda da API (em vez de erro genérico) quando faltarem PEIs.

- [ ] **Step 3: Acelerar `ConteudoChat.tsx` e `controller.ts`**

1. Em `components/app/chat/ConteudoChat.tsx`:
   - Quando `respondeuViaApiAutenticada === true`, não chamar `persistir()` duas vezes (a API já persistiu o turno); atualizar `setMensagensLazy` diretamente e invalidar apenas `["conversas"]` e `["student-insights"]` via `useQueryClient()` sem bloquear o `finally` com `await recarregar()`.
2. Em `utils/data/controller.ts`:
   - Remover leituras prévias redundantes (`listEscolas`, `listTurmas`, `listAlunos`) antes de `update*` e `delete*` onde a API já valida existência/permissão.

- [ ] **Step 4: Validar build e lint do frontend**

Run (em `C:\Users\kauas\OneDrive\Documentos\Projetos\MetamorphIA_Frontend-2` com `BypassSandbox: true`): `pnpm lint && pnpm build`
Expected: EXIT 0 sem erros de TypeScript ou lint.

---

## Self-Review Checklist (Executado)
- **Spec coverage:** `pgvector` por aluno (Task 1 & 3), controle Multi-PEI por todas as disciplinas do professor (Task 2, 5, 6), anti-burla *promptscript* + nome do aluno + trava "só adaptação" (Task 4), Modo Teste 1ª IA Analisadora com `%` (Task 5), remoção de `localStorage` de professores/perfil e aceleração Frontend+Backend (Task 3, 5, 6). Sem lacunas.
- **Type consistency:** `AlunoMemoriaVetorial`, `todos_peis_concluidos`, `disciplinas_pendentes`, `modo_teste_analisador`, `limiar_analisador`, `analise_entrada`, `todosConcluidos` consistentes entre todos os módulos.
- **No commits:** Nenhum comando `git commit` incluído no plano.
