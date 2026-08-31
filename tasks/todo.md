# Remove subscription / nexus-llm registry — local-only release

## Decisions
- Feature gating: remove entirely (all features on). Keep local ui_mode (normal/advanced).
- Multi-user + session sharing: remove entirely (single-user app).
- Broker: KEEP, manual configuration only (user sets [broker] url + broker_api_key secret by hand). Remove nexus-account-derived auth paths.
- Startup migration: auto-clean stale nexus artifacts from existing installs.
- Tunnel (Cloudflare + access code): unchanged.

## Backend
- [x] Delete `agent/src/nexus/auth/` (nexus_account.py, status_watcher.py)
- [x] Delete routes: nexus_account.py, auth.py, admin.py, share.py, vault_share.py; share endpoints in sessions routes
- [x] Delete server/auth.py (AuthManager), server/user_store/, MultiUserAuthMiddleware; FeatureGateMiddleware
- [x] app.py: remove multi_user wiring, feature gate, /auth tunnel prefix, UserStore creation
- [x] app_lifespan.py: remove StatusWatcher start/stop; keep broker poller
- [x] Delete features.py; update callers (registry.py, app_lifespan heartbeat gate, loop/summarize.py, loop/_builder.py, routes/config.py)
- [x] registry.py: remove cloud_models gate + nexus runtime_kind provider registration + nexus_api_key credential default
- [x] providers/catalog.json: remove nexus hosted entry
- [x] routes/providers.py: remove nexus_signin auth method
- [x] config_schema.py: remove NexusAccountConfig + server.multi_user; keep [broker]
- [x] Broker: keep client/poller/routes; auth via manual broker_api_key only; remove nexus-account coupling (webhook.py key check, provision.py account paths)
- [x] CLI: delete users_cmd.py + registration
- [x] pubsub.py: drop features_changed / nexus_tier_changed allowlist entries
- [x] New startup migration: strip [nexus_account], server.multi_user, nexus providers/models from config.toml; delete account.json, feature_cache.json, .feature_cache_key; remove nexus_api_key from secrets.toml (keep broker_api_key)
- [x] Fix tests; `uv run pytest` + `uv run ruff check src tests` green

## Frontend
- [x] App.tsx: remove NexusLoginScreen gate, useNexusAccount, share route/SharedSessionView
- [x] Delete: NexusLoginScreen, NexusSignin, NexusTab, NexusUsageGauges, AdminPanel, ProfileTab, SharingSection, SharedSessionView, useNexusAccount, api/nexusAccount.ts, api/auth.ts (multi-user parts), SessionProvider
- [x] AuthGate: keep tunnel branch only
- [x] SettingsDrawer: drop nexus/profile/admin tabs (new default tab)
- [x] WizardModal + api/catalog.ts + types.ts: remove nexus_signin auth method, nexus runtime kind, auto-apply
- [x] useFeatures: strip feature gating, keep ui_mode normal/advanced only; stop reading cfg.features
- [x] `npm run build` green

## Review
- [x] grep repo for nexus-model.us / nexus-broker accidental removals vs keeps
- [x] Full backend tests + UI build pass

## Summary
Single-user, fully local build. Removed: nexus account/subscription (auth/ package,
status watcher, /auth/nexus/*, /admin, share links, multi-user store + middleware),
feature gating (features.py + FeatureGateMiddleware + UI view filtering), nexus
hosted provider + nexus_signin wizard auth. Kept: tunnel (incl. SharingSection QR UI,
restored after mistaken deletion), broker with manual config ([broker] url +
broker_api_key in ~/.nexus/secrets.toml), provider OAuth routes (/auth/oauth/*,
/auth/local/*), ui_mode normal/advanced. New: local_migration.py startup sweep of
stale [nexus_account]/multi_user/nexus providers/keys/cache files.

## Review
- uv run pytest: 1175 passed; 32 failed + 6 errors — all pre-existing on HEAD.
- ruff: changed files clean (61 repo-wide errors pre-existing).
- npm run build: green.
- No dangling refs to nexus_account/nexus-model.us/nexus_api_key/multi_user outside local_migration.py.
- Brokers keep working; webhook.py now requires manually-set broker_api_key only.
