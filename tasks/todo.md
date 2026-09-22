# Fila de mensagens com injeção mid-turn (queue-then-inject)

## Semântica
- Enter durante processamento → mensagem entra em fila por sessão → injetada como user message na próxima fronteira de lote de tool calls do turno corrente.
- Turno termina com fila não-vazia → runner encadeia novo turno automaticamente.
- Mensagens removíveis enquanto não processadas (chip com X).
- v1: text-only (sem anexos); slash commands bloqueados durante turno.

## Backend
- [x] `ChatTurnRunner`: fila `queue: list[QueuedInput{qid,text}]`, `enqueue()/remove_queued()/drain_pending()`, encadeamento pós-turno em `_run` (checagem síncrona via `_finalized`), `turn_settled` no finally, fix do pop sem identity check.
- [x] `chat_stream.py`: branch de enqueue quando `get_running_turn(sid)` ativo; SSE curto `event: queued`; slash/anexo durante turno → 409 `turn_active`; break do subscriber passa a ser em `turn_settled`.
- [x] `agent.py::_handle_tool_exec_result`: callback `drain_pending_inputs`; guarda `_tool_batch_complete` (ids do último assistant ⊆ tool msgs respondidas); append USER + yield `user_injected` + `_restart_loom_stream` (cancela o `__anext__` in-flight — fix de chamada LLM desperdiçada, vale também para compaction).
- [x] `chat.py`: `DELETE /chat/{sid}/queue/{qid}` → `queue_removed` no bus; break do `turn/stream` em `turn_settled`.
- [x] `_streaming.py`: TurnAccumulator encaminha `user_enqueued`/`user_injected`/`queue_removed`/`turn_settled`.

## Frontend (ui/)
- [x] `api/chat.ts`: tipos novos + parser + `deleteQueuedMessage`.
- [x] `useChatSession.send()`: caminho de fila quando `thinking` (hidden seeds/inPlace/anexos/slash não enfileiram); ack `queued` seta qid; falha devolve texto ao input.
- [x] Dispatcher com chave mutável (`streamKey`/`streamActiveSession`): turnos encadeados após migração NEW_KEY→sid roteiam certo; `user_injected` insere bubble antes do assistant streaming (ou cria placeholder quando encadeado) e re-arma `thinking`.
- [x] `handleStop` limpa chips localmente; `handleRemoveQueued` (DELETE otimista).
- [x] `InputBar`: textarea/Enter habilitados durante busy; upload desabilitado busy; `QueueBar` com chips + X + spinner pré-ack (`InputBar.css`).
- [x] i18n en/pt-BR (`chat:input.queueRemove`).

## Chrome panel
- [x] `panel.js`: input habilitado durante streaming; `sendQueued()`; `renderQueue()`/`removeQueued()`; `onQueueEvent` no canal de eventos (dedupe natural — POST stream e events channel não duplicam pois só o events channel trata os eventos de fila).
- [x] `panel.html` `#queueBar`; `panel.css` chips.

## Testes
- [x] `tests/test_chat_queue.py` (5): injeção mid-turn (provider vê a user msg injetada na 2ª chamada), encadeamento (2× done + user_injected depois do 1º done), remoção via DELETE, sem runner paralelo, slash → 409.
- [x] `ruff check src tests` ✓; `uv run pytest` completo: **1343 passed, 16 skipped** (live tests ignorados); `npm run build` ✓; `node --check panel.js` ✓.

## Review
- Corrida fim-de-turno resolvida por construção: `_next_queued()` (sync, sem await entre check e `_finalized=True`) × `enqueue()` (sync) — ack nunca é perdido; POST pós-finalização cai no turno normal.
- Leak pré-existente corrigido de passagem: o loop agendava o próximo `__anext__` antes de despachar o evento; todo restart (compaction incl.) desperdiçava uma chamada LLM — agora `_restart_loom_stream` cancela o task in-flight.
- Bugs pré-existentes corrigidos: pop de `_running_turns` sem identity check; POSTs concorrentes durante turno (corrupção last-writer-wins) agora impossíveis por construção (409/enqueue no lugar de loop paralelo).
- Limitações v1 (deliberadas): anexos e slash não enfileiram; continuação HITL (`continue_after_hitl`) não registra runner (fora de `_running_turns`) — enqueue durante esse fluxo cai no turno normal (comportamento pré-existente, não piorado); fila vive em memória do runner (perdida em restart do server — aceitável, mensagem nunca é ackada nesse caso).
