# packaging/build.ps1 — Windows analog of build.sh.
#
# Produces a self-contained Nexus distribution under dist\Nexus\ that ships
# its own Python interpreter, all dependencies, the built UI, the bundled
# skills, pre-downloaded embedding/spaCy models, and a tray launcher.
#
# Usage (PowerShell 7+, run from the repo root):
#   .\packaging\build.ps1
#   .\packaging\build.ps1 -SkipModels
#   .\packaging\build.ps1 -BundleLlm none|qwen-3b|gemma-e4b
#   .\packaging\build.ps1 -DemoUrl https://... -DemoKey sk-... -DemoModel nexus
#
# Output:
#   dist\Nexus\                 — extract / copy this folder anywhere
#   dist\Nexus.zip              — same, zipped (created last)
#
# Requirements on the build host:
#   - PowerShell 7 (pwsh) — the script uses modern cmdlets and -ErrorAction
#   - Node.js + npm (for the UI build)
#   - A sibling loom checkout at ..\loom (or pass -LoomDir)
#   - Internet access on first run (downloads CPython + optional models)
#
# This script does NOT codesign. Sign with signtool.exe afterward if needed.

[CmdletBinding()]
param(
    [switch]$SkipModels,
    [ValidateSet('none', 'qwen-3b', 'gemma-e4b')]
    [string]$BundleLlm = 'none',
    [string]$DemoUrl = $env:DEMO_LLM_BASE_URL,
    [string]$DemoKey = $env:DEMO_LLM_API_KEY,
    [string]$DemoModel = $env:DEMO_LLM_MODEL,
    [string]$LoomDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ── Paths ────────────────────────────────────────────────────────────────────
$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Packaging  = Join-Path $RepoRoot 'packaging'
$WindowsPkg = Join-Path $Packaging 'windows'
if (-not $LoomDir) { $LoomDir = Join-Path (Split-Path $RepoRoot -Parent) 'loom' }

$Dist  = Join-Path $RepoRoot 'dist'
$Stage = Join-Path $Dist 'stage'
$App   = Join-Path $Dist 'Nexus'

# ── Constants — keep aligned with build.sh ───────────────────────────────────
$PyVersion = '3.12.7'
$PyBuild   = '20241016'
$PyTriple  = 'x86_64-pc-windows-msvc'
$PyDist    = "cpython-$PyVersion+$PyBuild-$PyTriple-install_only.tar.gz"
$PyUrl     = "https://github.com/astral-sh/python-build-standalone/releases/download/$PyBuild/$PyDist"

$LlamaTag  = 'b8929'
# Win64 CPU-only build is the safest default — works on every machine. Users
# with a real GPU can swap in the cuda/vulkan archive by hand and rerun.
$LlamaDist = "llama-$LlamaTag-bin-win-cpu-x64.zip"
$LlamaUrl  = "https://github.com/ggerganov/llama.cpp/releases/download/$LlamaTag/$LlamaDist"

# ── Sanity ───────────────────────────────────────────────────────────────────
if (-not (Test-Path $LoomDir)) {
    throw "loom not found at $LoomDir (set -LoomDir or place loom alongside nexus)"
}

# Demo-model triple must be all-or-nothing.
$demoSet = @($DemoUrl, $DemoKey, $DemoModel) | Where-Object { $_ }
if ($demoSet.Count -ne 0 -and $demoSet.Count -ne 3) {
    throw '-DemoUrl, -DemoKey, -DemoModel must all be set together (or all unset)'
}

switch ($BundleLlm) {
    'none'      { $LlmRepo = ''; $LlmFile = ''; $LlmName = '' }
    'qwen-3b'   {
        $LlmRepo = 'bartowski/Qwen2.5-3B-Instruct-GGUF'
        $LlmFile = 'Qwen2.5-3B-Instruct-Q4_K_M.gguf'
        $LlmName = 'qwen2.5-3b-instruct'
    }
    'gemma-e4b' {
        $LlmRepo = 'bartowski/google_gemma-3n-E4B-it-GGUF'
        $LlmFile = 'google_gemma-3n-E4B-it-Q4_K_M.gguf'
        $LlmName = 'gemma-3n-e4b'
    }
}

# ── Clean ────────────────────────────────────────────────────────────────────
Write-Host "==> Cleaning $Dist"
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
New-Item $Stage -ItemType Directory -Force | Out-Null

# ── UI build ─────────────────────────────────────────────────────────────────
Write-Host '==> Building UI (npm run build)'
Push-Location (Join-Path $RepoRoot 'ui')
try {
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' }
} finally {
    Pop-Location
}
$uiOut = Join-Path $Stage 'ui'
New-Item $uiOut -ItemType Directory -Force | Out-Null
Copy-Item -Path (Join-Path $RepoRoot 'ui\dist\*') -Destination $uiOut -Recurse -Force

# ── Standalone CPython ───────────────────────────────────────────────────────
Write-Host '==> Fetching standalone CPython'
$cache = Join-Path $Dist '.cache'
New-Item $cache -ItemType Directory -Force | Out-Null
$pyArchive = Join-Path $cache $PyDist
if (-not (Test-Path $pyArchive)) {
    Invoke-WebRequest -Uri $PyUrl -OutFile $pyArchive
}
$pyStage = Join-Path $Stage 'python'
New-Item $pyStage -ItemType Directory -Force | Out-Null
# python-build-standalone ships a top-level "python\" folder inside the tar;
# tar's --strip-components flattens that so our layout matches the macOS bundle.
tar -xzf $pyArchive -C $pyStage --strip-components=1
if ($LASTEXITCODE -ne 0) { throw 'tar extract failed (need a tar.exe — included on Windows 10+ or via Git for Windows)' }
$Py = Join-Path $pyStage 'python.exe'
if (-not (Test-Path $Py)) { throw "python.exe not found at $Py — archive layout mismatch" }
& $Py --version

# ── pip install nexus + loom + ddgs ──────────────────────────────────────────
Write-Host '==> Installing nexus + dependencies into bundled Python'
& $Py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
# ddgs fork pinned in pyproject [tool.uv.sources]; pip doesn't honor that.
& $Py -m pip install 'git+https://github.com/NinoCoelho/ddgs'
if ($LASTEXITCODE -ne 0) { throw 'ddgs install failed' }
& $Py -m pip install "$LoomDir[anthropic,acp,tui,graphrag,search,scrape]"
if ($LASTEXITCODE -ne 0) { throw 'loom install failed' }
& $Py -m pip install "$RepoRoot\agent[pdf]"
if ($LASTEXITCODE -ne 0) { throw 'nexus install failed' }
# pystray + Pillow power the tray launcher (mirrors the macOS Swift menu bar).
# Pillow may already be present via the [pdf] extra; pip is a no-op if so.
& $Py -m pip install pystray Pillow
if ($LASTEXITCODE -ne 0) { throw 'tray launcher deps install failed' }
& $Py -m pip install pywebview
if ($LASTEXITCODE -ne 0) { throw 'pywebview install failed' }

# Move site-packages into the staged layout expected by tray.pyw / bootstrap.py.
# python-build-standalone on Windows places site-packages under
# Lib\site-packages (vs. lib/python3.12/site-packages on POSIX).
$siteSrc = Get-ChildItem -Path (Join-Path $pyStage 'Lib') -Filter 'site-packages' -Directory -Recurse |
    Select-Object -First 1
if (-not $siteSrc) { throw 'could not locate site-packages under bundled Python' }
$siteDst = Join-Path $Stage 'site-packages'
New-Item $siteDst -ItemType Directory -Force | Out-Null
Copy-Item -Path (Join-Path $siteSrc.FullName '*') -Destination $siteDst -Recurse -Force

# ── Bytecode-only nexus + loom ───────────────────────────────────────────────
Write-Host '==> Stripping .py sources from nexus + loom (bytecode-only)'
& $Py -m compileall -b -q -f --invalidation-mode unchecked-hash `
    (Join-Path $siteDst 'nexus') (Join-Path $siteDst 'loom')
if ($LASTEXITCODE -ne 0) { throw 'compileall failed' }
Get-ChildItem -Path (Join-Path $siteDst 'nexus'), (Join-Path $siteDst 'loom') `
    -Recurse -Filter '*.py' | Remove-Item -Force
Get-ChildItem -Path (Join-Path $siteDst 'nexus'), (Join-Path $siteDst 'loom') `
    -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

# ── Pre-downloaded models (optional) ─────────────────────────────────────────
$modelsDir = Join-Path $Stage 'models'
if (-not $SkipModels) {
    Write-Host '==> Pre-downloading embedding models into bundle'
    New-Item (Join-Path $modelsDir 'fastembed') -ItemType Directory -Force | Out-Null
    $env:NEXUS_MODELS_DIR = $modelsDir
    $script = @"
import os
from pathlib import Path
models = Path(os.environ['NEXUS_MODELS_DIR'])
(models / 'fastembed').mkdir(parents=True, exist_ok=True)
from fastembed import TextEmbedding
TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir=str(models / 'fastembed'))
print('fastembed cached at', models / 'fastembed')
"@
    & $Py -c $script
    if ($LASTEXITCODE -ne 0) { throw 'fastembed prefetch failed' }

    Write-Host '==> Pre-downloading spaCy en_core_web_sm into bundle'
    & $Py -m spacy download en_core_web_sm
    if ($LASTEXITCODE -ne 0) { throw 'spaCy download failed' }
    $spacyPkg = (& $Py -c 'import en_core_web_sm, os; print(os.path.dirname(en_core_web_sm.__file__))').Trim()
    $spacyDst = Join-Path $modelsDir 'spacy'
    New-Item $spacyDst -ItemType Directory -Force | Out-Null
    Copy-Item -Path $spacyPkg -Destination (Join-Path $spacyDst 'en_core_web_sm_pkg') -Recurse -Force
    Write-Host "spaCy cached at $spacyDst\en_core_web_sm_pkg"
}

# ── Bundled local LLM (optional) ─────────────────────────────────────────────
if ($LlmRepo) {
    Write-Host "==> Fetching llama.cpp ($LlamaTag, win-cpu-x64)"
    $llamaCache = Join-Path $cache $LlamaDist
    if (-not (Test-Path $llamaCache)) {
        Invoke-WebRequest -Uri $LlamaUrl -OutFile $llamaCache
    }
    $llamaDst = Join-Path $Stage 'llama'
    New-Item $llamaDst -ItemType Directory -Force | Out-Null
    Expand-Archive -Path $llamaCache -DestinationPath $llamaDst -Force
    $llamaServer = Get-ChildItem -Path $llamaDst -Filter 'llama-server.exe' -Recurse |
        Select-Object -First 1
    if (-not $llamaServer) { throw 'llama-server.exe not found in archive' }
    $llamaRel = $llamaServer.FullName.Substring($llamaDst.Length).TrimStart('\') -replace '\\', '/'
    Write-Host "llama-server at llama/$llamaRel"

    Write-Host "==> Fetching $LlmName GGUF (largest download — minutes on slow links)"
    $llmDst = Join-Path $modelsDir 'llm'
    New-Item $llmDst -ItemType Directory -Force | Out-Null
    $hfUrl = "https://huggingface.co/$LlmRepo/resolve/main/$LlmFile"
    $ggufCache = Join-Path $cache $LlmFile
    if (-not (Test-Path $ggufCache)) {
        Invoke-WebRequest -Uri $hfUrl -OutFile $ggufCache
    }
    Copy-Item $ggufCache (Join-Path $llmDst $LlmFile) -Force

    $llmManifest = @{
        binary     = "llama/$llamaRel"
        model_file = "models/llm/$LlmFile"
        model_name = $LlmName
        ctx_size   = 16384
    } | ConvertTo-Json -Compress
    Set-Content -Path (Join-Path $Stage 'llm.json') -Value $llmManifest -Encoding utf8NoBOM
}

# ── Bundled skills ───────────────────────────────────────────────────────────
$skillsSrc = Join-Path $RepoRoot 'skills'
if (Test-Path $skillsSrc) {
    Write-Host '==> Staging bundled skills'
    $skillsDst = Join-Path $Stage 'skills'
    New-Item $skillsDst -ItemType Directory -Force | Out-Null
    # Mirror what rsync --exclude=.DS_Store --exclude=SKILL_FORMAT.md does on macOS.
    Get-ChildItem -Path $skillsSrc -Recurse |
        Where-Object { $_.Name -ne '.DS_Store' -and $_.Name -ne 'SKILL_FORMAT.md' } |
        ForEach-Object {
            $rel = $_.FullName.Substring($skillsSrc.Length + 1)
            $dest = Join-Path $skillsDst $rel
            if ($_.PSIsContainer) {
                New-Item $dest -ItemType Directory -Force | Out-Null
            } else {
                $parent = Split-Path $dest -Parent
                if (-not (Test-Path $parent)) { New-Item $parent -ItemType Directory -Force | Out-Null }
                Copy-Item $_.FullName $dest -Force
            }
        }
}

# ── Demo-model manifest (optional) ───────────────────────────────────────────
if ($DemoUrl -and $DemoKey -and $DemoModel) {
    Write-Host "==> Staging demo_llm.json (model: $DemoModel)"
    $demo = @{
        base_url   = $DemoUrl
        api_key    = $DemoKey
        model_name = $DemoModel
    } | ConvertTo-Json -Compress
    $demoOut = Join-Path $Stage 'demo_llm.json'
    Set-Content -Path $demoOut -Value $demo -Encoding utf8NoBOM
    # NTFS ACLs aren't honored across zip extraction, but lock it down on
    # the build host anyway so any local snooping is blocked.
    $acl = Get-Acl $demoOut
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME, 'FullControl', 'Allow')
    $acl.SetAccessRule($rule)
    Set-Acl $demoOut $acl
}

# ── Launcher + bootstrap ────────────────────────────────────────────────────
# bootstrap.py is shared with the macOS Swift host; tray.pyw is the source
# we feed to PyInstaller below; Nexus.cmd is kept as a SmartScreen-fallback
# entry point for users whose policy blocks unsigned .exe on first run.
Copy-Item (Join-Path $Packaging 'bootstrap.py') (Join-Path $Stage 'bootstrap.py') -Force
Copy-Item (Join-Path $WindowsPkg 'tray.pyw')     (Join-Path $Stage 'tray.pyw')     -Force
Copy-Item (Join-Path $WindowsPkg 'Nexus.cmd')    (Join-Path $Stage 'Nexus.cmd')    -Force

# ── Build Nexus.exe via PyInstaller ──────────────────────────────────────────
# We build inside a throwaway venv so PyInstaller + altgraph + hooks-contrib
# never leak into the shipped site-packages. The venv inherits the bundled
# python-build-standalone interpreter via pyvenv.cfg, so the produced exe
# embeds the same CPython we ship — no Python version mismatch between the
# tray launcher and the bundled server runtime.
Write-Host '==> Building Nexus.exe via PyInstaller (throwaway venv)'
$BuildVenv = Join-Path $cache 'build-venv'
if (Test-Path $BuildVenv) { Remove-Item $BuildVenv -Recurse -Force }
& $Py -m venv $BuildVenv
if ($LASTEXITCODE -ne 0) { throw 'venv create failed' }
$BuildPy = Join-Path $BuildVenv 'Scripts\python.exe'
& $BuildPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade in build venv failed' }
# pystray + Pillow have to be importable when PyInstaller scans the source —
# its modulegraph follows real imports, not string analysis.
& $BuildPy -m pip install pyinstaller pystray Pillow pywebview
if ($LASTEXITCODE -ne 0) { throw 'pyinstaller install failed' }

# Generate a multi-resolution .ico the same way the tray icon is drawn at
# runtime, so taskbar / Explorer / tray all show the same mark. PIL's .ico
# encoder writes every requested size into a single file.
$icoPath = Join-Path $Stage 'Nexus.ico'
$icoScript = @"
from PIL import Image, ImageDraw
import sys
img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((24, 24, 232, 232), radius=56, fill=(46, 92, 196, 255))
d.ellipse((96, 96, 160, 160), fill=(255, 255, 255, 255))
img.save(sys.argv[1], format='ICO',
         sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
"@
& $BuildPy -c $icoScript $icoPath
if ($LASTEXITCODE -ne 0) { throw 'icon generation failed' }

# Stage tray.pyw under a .py extension so PyInstaller's argv parsing doesn't
# trip on the rare ``.pyw`` registry redirect on locked-down hosts.
$traySrc = Join-Path $cache 'tray.py'
Copy-Item (Join-Path $WindowsPkg 'tray.pyw') $traySrc -Force

$piWork = Join-Path $cache 'pyinstaller-work'
$piDist = Join-Path $cache 'pyinstaller-dist'
if (Test-Path $piWork) { Remove-Item $piWork -Recurse -Force }
if (Test-Path $piDist) { Remove-Item $piDist -Recurse -Force }

& $BuildPy -m PyInstaller `
    --noconfirm `
    --onefile `
    --noconsole `
    --name Nexus `
    --icon $icoPath `
    --workpath $piWork `
    --distpath $piDist `
    --specpath $cache `
    $traySrc
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

$exeOut = Join-Path $piDist 'Nexus.exe'
if (-not (Test-Path $exeOut)) { throw "Nexus.exe not produced at $exeOut" }
Copy-Item $exeOut (Join-Path $Stage 'Nexus.exe') -Force

# Clean up — the .ico isn't needed at runtime (it's embedded in the exe),
# and the build venv would just bloat the .cache between builds.
Remove-Item $icoPath -Force
Remove-Item $BuildVenv -Recurse -Force
Remove-Item $piWork -Recurse -Force
Remove-Item $piDist -Recurse -Force

# loom commit pin for diagnostics (parity with macOS bundle).
try {
    Push-Location $LoomDir
    git rev-parse HEAD | Set-Content (Join-Path $Stage 'loom_version.txt') -Encoding utf8NoBOM
} catch {
    Write-Warning 'could not record loom version'
} finally {
    Pop-Location
}

# ── Assemble final output ────────────────────────────────────────────────────
Write-Host "==> Assembling $App"
New-Item $App -ItemType Directory -Force | Out-Null
# Preserve directory structure with a recursive copy of everything in stage.
Copy-Item -Path (Join-Path $Stage '*') -Destination $App -Recurse -Force

# Optional: produce a portable zip alongside the folder. Useful for
# distribution; users extract anywhere and double-click Nexus.cmd.
$zip = Join-Path $Dist 'Nexus.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "==> Compressing $zip"
Compress-Archive -Path $App -DestinationPath $zip -CompressionLevel Optimal

$size = (Get-ChildItem $App -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host ("==> Done: {0} ({1:N1} GB)" -f $App, ($size / 1GB))
