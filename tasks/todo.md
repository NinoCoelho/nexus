# Multi-channel chat: reliability + origin — 2026-11-20

## Fase 0 — Event delivery (spinner eterno + HITL invisível)
- [x] Payload do `done` serializável na origem (`chat_turn_runner._publish` via `events.json_safe`)
- [x] `to_sse()` à prova de falha (frame ruim → log + placeholder; rota de events pula frame em vez de morrer)
- [x] `GET /sessions/{id}` expõe HITL pendente (`pending[]`)
- [x] Painel Chrome renderiza prompt pendente ao abrir (mesmo caminho do `user_request`, dedup por request_id)
- [x] Web UI renderiza prompt pendente ao abrir (via `useApprovalQueue.injectRequest` → mesmo ApprovalDialog)

## Iteração 2 — Transcrição limpa em todos os canais (feedback iOS)
- [x] `server/message_display.py`: strip do `<context>` + marcador `<!-- nx:team -->` **nessa ordem** (na sessão do @hype o marcador precede o bloco e quebrava o strip por âncora)
- [x] `GET /sessions/{id}`: rows `tool` omitidas por default (web pede `?include_tools=1`); user content limpo; campos `team_pass` e `page {url,title}`
- [x] Ecos ao vivo (`turn_started`/`user_injected`/`user_enqueued`) limpos com `team_pass`
- [x] `_streaming` (whitelist) volta a entregar `origin` + `team_pass` nos frames
- [x] Títulos: derivação limpa, self-heal de título poluído no próximo write, backfill correu (20/20 sessões)
- [x] Web: `getSession?include_tools=1`, chip team-pass via campo (fallback marcador), tipos
- [x] 15 testes em test_multichannel_origin.py (inclui o caso exato do @hype); cascade/telegram/sse em dia (yolo é pre-existing no HEAD)
- [x] `npm run build` ok; verificado ao vivo na sessão: 4 msgs visíveis, 0 `<context>`, título "lista meus seguidores"

## Fase 1 — Reload nunca perde a sessão
- [x] Promoção `__new__` → id real no 1º frame SSE (server adota o id verbatim; recovery paths re-keyed)
- [x] Last-active session em `localStorage("nexus.lastSessionId")` + restauração no boot (rota default) + re-attach `/turn/*`

## Fase 2 — Origem por canal
- [x] `ChatRequest.surface` (web | chrome-extension | ios); web, painel (3 call sites) e iOS enviam
- [x] Runner deixa de hardcodar origin "web" (usa surface; fila idem; telegram via launcher marca "telegram")
- [x] Coluna `messages.origin` (migração idempotente) + sobrevive a rewrites (merge por índice com snapshot pré-rewrite)
- [x] `GET /sessions/{id}` retorna `origin` por mensagem; contexto da sessão prefixado ("Chrome side panel" / "iOS app")
- [x] Badges no painel e na web (só quando origem ≠ canal atual; labels Chrome/iOS/Telegram)

## Fase 3 — CDP recusado no plugin
- [x] ContextVar `TURN_SURFACE` setada pelo runner (herda por task p/ tools e prompt)
- [x] Gate no `BrowserHandler.invoke` + `[browser].extension_policy = refuse|ask|allow` (default refuse; `_cfg_to_dict` serializa; parse por tabela inteira)
- [x] Bloco de prompt p/ superfície chrome-extension (zona volátil) + rewording do param `js` do page tool e da skill chrome-devtools (repo + cópia viva em ~/.nexus/skills)

## Verificação
- [x] `uv run pytest --ignore=tests/live`: 1983 passed; 6 fails/6 errors pre-existing no HEAD (comprovado em worktree do HEAD) ou live
- [x] Ruff: nenhum erro novo (E/F) introduzido; repo não é ruff-clean no baseline
- [x] `node --check chrome/panel.js` / `npm run build` (tsc + vite ok)
- [ ] Repro manual (reload mid-turn, resposta em outro canal, CDP recusado no painel) — precisa `nexus daemon restart` + reload da extensão

## Futuro (fora do escopo agora)
- Persistir resposta parcial em tool-boundaries; persistir fila entre restarts; badges no iOS
- Testes pre-existing quebrados no HEAD: test_server_sse yolo, test_anthropic_oauth_headers (vale investigar à parte)

## Resumo
34 arquivos, +748/−186. Contrato: `POST /chat/stream {surface}`, `GET /sessions/{id}` → `messages[].origin` + `pending[]`, eventos de turno carregam `origin` fiel. O bug do spinner era o `done` com `ChatMessage` cru chegando ao `json.dumps` do canal SSE — corrigido na origem e defensivamente no `to_sse` + rota.
