# Windows packaging

`packaging/build.ps1` produces a self-contained Nexus distribution under
`dist\Nexus\` (and a sibling `dist\Nexus.zip`) that ships bundled CPython,
all Python deps, the built UI, the bundled skills, pre-downloaded
embedding/spaCy models, and a tray launcher. It is the Windows analog of
`packaging/build.sh`.

## Build host requirements

- PowerShell 7+ (`pwsh`)
- Node.js + npm (UI build)
- A working `tar.exe` (Windows 10+ ships one; Git for Windows also bundles one)
- A sibling loom checkout at `..\loom` (or pass `-LoomDir`)
- Internet access on first run (downloads CPython + optional models)

## Build

```powershell
# From the repo root.
.\packaging\build.ps1
.\packaging\build.ps1 -SkipModels                 # skip embedding/spaCy prefetch
.\packaging\build.ps1 -BundleLlm qwen-3b          # bundle a local llama.cpp + Qwen2.5-3B
.\packaging\build.ps1 -DemoUrl https://... `
                      -DemoKey sk-... `
                      -DemoModel nexus            # fresh-install demo provider
```

Output:

```
dist\
  Nexus\          ← extract / copy this folder anywhere; users double-click Nexus.cmd
  Nexus.zip       ← the same, zipped, suitable for distribution
```

## Layout (mirrors the macOS `.app` resources)

```
Nexus\
  Nexus.exe              ← canonical entry point (PyInstaller-frozen tray.pyw)
  Nexus.cmd              ← SmartScreen-fallback entry point
  python\python.exe      ← bundled CPython (python-build-standalone)
  python\pythonw.exe     ← console-less Python used by the tray
  site-packages\         ← nexus + loom + all deps (bytecode-only)
  ui\index.html          ← built UI dist
  models\fastembed\      ← pre-cached embedder
  models\spacy\          ← pre-cached spaCy model
  models\llm\*.gguf      ← optional, when -BundleLlm is set
  llama\llama-server.exe ← optional, when -BundleLlm is set
  llm.json               ← optional manifest read by bootstrap.py
  skills\                ← bundled builtin skills (seeded into ~/.nexus on first run)
  bootstrap.py           ← shared launcher (also used by macOS Swift host)
  tray.pyw               ← Windows tray UI source (frozen into Nexus.exe)
```

The launcher contract is identical to macOS: the tray (`Nexus.exe` or
`tray.pyw` directly via `Nexus.cmd`) spawns `python.exe bootstrap.py`;
bootstrap chooses a port, writes it to `.port` next to itself, and the
tray polls `.port` + `/health` before opening the browser.

`Nexus.exe` is built by PyInstaller (`--onefile --noconsole`) inside a
throwaway venv during the build, so PyInstaller and its hooks never leak
into the shipped `site-packages\`. The icon (`Nexus.ico`) is generated
procedurally with PIL at build time — no binary asset to commit.

## Code signing

The script does not sign the bundle. Sign with `signtool.exe` afterward
when distributing publicly:

```powershell
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 `
    "dist\Nexus\Nexus.exe" `
    "dist\Nexus\python\python.exe" `
    "dist\Nexus\python\pythonw.exe"
```

Signing `Nexus.exe` is the most important — it's the one that triggers
SmartScreen / Defender warnings on a fresh install. Unsigned, users see
a "Windows protected your PC" dialog on first run.

## Updating an installed copy

The bundle is a portable folder — replace `Nexus\` in place to upgrade.
Per-user state in `%USERPROFILE%\.nexus\` is preserved across upgrades.
