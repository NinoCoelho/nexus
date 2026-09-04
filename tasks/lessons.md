
## 2026-09-04 — graphrag reindex failure (user-reported)
- **Pattern:** When refactoring label/type lookup maps consumed by NLP model output, enumerate the FULL label set from every model variant in play (en_core_web_sm AND pt_core_news_sm emit different labels — pt has MISC). Never use bare `MAP[label]` on model-produced labels; always `.get()` with a fallback.
- **Pattern:** Test the non-default language path: a change verified only against en CoreWeb silently broke the pt pipeline (the user's vault is largely Portuguese).
- **Pattern:** Long-running CLI commands must show progress (percent + running totals) from the first minute. Reuse existing streaming generators instead of the silent full-scan variant — "no output" reads as "frozen/failed" to users.
