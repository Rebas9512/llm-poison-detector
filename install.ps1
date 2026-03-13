# ──────────────────────────────────────────────────────────────────────────────
#  LLM Poison Detector — Windows One-liner Installer
#
#  irm https://raw.githubusercontent.com/Rebas9512/llm-poison-detector/main/install.ps1 | iex
#
#  To pass parameters use the scriptblock form:
#    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Rebas9512/llm-poison-detector/main/install.ps1))) -NoSetup
#
#  Parameters:
#    -InstallDir <path>   Install directory  (default: $HOME\llm-poison-detector)
#    -NoSetup             Skip the first-run environment check entirely
#    -AutoBackbone        Auto-download default backbone without prompting
#                         (sets LLP_AUTO_BACKBONE=1 for check_env.py)
#  Environment variables:
#    LLP_DIR              Override the install directory
#    LLP_REPO_URL         Override the git clone URL
#    LLP_NO_SETUP=1       Skip the first-run environment check entirely
#    LLP_AUTO_BACKBONE=1  Auto-download default backbone without prompting
# ──────────────────────────────────────────────────────────────────────────────
param(
    [string]$InstallDir = "",
    [switch]$NoSetup,
    [switch]$AutoBackbone
)

$ErrorActionPreference = "Stop"
$ConfigDir = Join-Path $env:USERPROFILE ".llmpoison"
$InstallMetaPath = Join-Path $ConfigDir "install.json"
$DefaultInstallDir = Join-Path $env:USERPROFILE "llm-poison-detector"
$InstallDirRedirectedFrom = $null

# ANSI colours — supported in Windows Terminal and PowerShell 7+
$GREEN  = "`e[38;2;0;229;180m"
$YELLOW = "`e[38;2;255;176;32m"
$RED    = "`e[38;2;230;57;70m"
$MUTED  = "`e[38;2;110;120;148m"
$BOLD   = "`e[1m"
$NC     = "`e[0m"

function Write-Ok($msg)      { Microsoft.PowerShell.Utility\Write-Host "${GREEN}√${NC}  $msg" }
function Write-Info($msg)    { Microsoft.PowerShell.Utility\Write-Host "${MUTED}·${NC}  $msg" }
function Write-Warn($msg)    { Microsoft.PowerShell.Utility\Write-Host "${YELLOW}!${NC}  $msg" }
function Write-Section($msg) { Microsoft.PowerShell.Utility\Write-Host ""; Microsoft.PowerShell.Utility\Write-Host "${BOLD}── $msg ──${NC}" }
function Write-Fail($msg)    { Microsoft.PowerShell.Utility\Write-Host "${RED}x${NC}  $msg"; exit 1 }

function Test-DirHasEntries([string]$Dir) {
    if (-not (Test-Path $Dir -PathType Container)) { return $false }
    return $null -ne (Get-ChildItem -Force -LiteralPath $Dir | Select-Object -First 1)
}

$SkipSetup = $NoSetup -or $env:LLP_NO_SETUP -eq "1"
$ModelsSetup = $false

if (-not $InstallDir) {
    if ($env:LLP_DIR) {
        $InstallDir = $env:LLP_DIR
    } else {
        $canPrompt = $true
        try {
            $canPrompt = -not [Console]::IsInputRedirected
        } catch {
            $canPrompt = $true
        }
        if ($canPrompt) {
            $raw = Read-Host "Install directory [$DefaultInstallDir]"
            if ($raw) {
                $InstallDir = $raw
            } else {
                $InstallDir = $DefaultInstallDir
            }
        } else {
            $InstallDir = $DefaultInstallDir
        }
    }
}

$InstallDir = $InstallDir.Trim()
if ($InstallDir.StartsWith('~\')) {
    $InstallDir = Join-Path $env:USERPROFILE $InstallDir.Substring(2)
} elseif ($InstallDir -eq "~") {
    $InstallDir = $env:USERPROFILE
}

$resolvedInstallDir = [IO.Path]::GetFullPath($InstallDir)
$resolvedConfigDir = [IO.Path]::GetFullPath($ConfigDir)
if ($resolvedInstallDir.TrimEnd('\') -eq $resolvedConfigDir.TrimEnd('\')) {
    Write-Fail "Install directory cannot be $ConfigDir (reserved for config files)."
}

if ((Test-Path $resolvedInstallDir) -and -not (Test-Path $resolvedInstallDir -PathType Container)) {
    Write-Fail "Install directory exists but is not a directory: $resolvedInstallDir"
}

if (-not (Test-Path (Join-Path $resolvedInstallDir ".git"))) {
    if ((Test-Path $resolvedInstallDir -PathType Container) -and (Test-DirHasEntries $resolvedInstallDir)) {
        $fallback = [IO.Path]::GetFullPath((Join-Path $resolvedInstallDir "llm-poison-detector"))
        if ((Test-Path $fallback) -and -not (Test-Path $fallback -PathType Container)) {
            Write-Fail "Fallback install directory exists but is not a directory: $fallback"
        }
        if ((Test-Path $fallback -PathType Container) -and -not (Test-Path (Join-Path $fallback ".git")) -and (Test-DirHasEntries $fallback)) {
            Write-Fail "Install directory $resolvedInstallDir already exists and is not empty. The fallback subdirectory $fallback also exists and is not empty."
        }
        $InstallDirRedirectedFrom = $resolvedInstallDir
        $resolvedInstallDir = $fallback
    }
}

$InstallDir = $resolvedInstallDir

Microsoft.PowerShell.Utility\Write-Host ""
Microsoft.PowerShell.Utility\Write-Host "${BOLD}  LLM Poison Detector — Installer${NC}"
Microsoft.PowerShell.Utility\Write-Host "${MUTED}  Install path: $InstallDir${NC}"
Microsoft.PowerShell.Utility\Write-Host "${MUTED}  Config path:  $ConfigDir${NC}"
Microsoft.PowerShell.Utility\Write-Host ""

# ── Execution policy ──────────────────────────────────────────────────────────
$policy = Get-ExecutionPolicy
if ($policy -eq "Restricted" -or $policy -eq "AllSigned") {
    try {
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force
        Write-Info "Execution policy set to RemoteSigned for this session."
    } catch {
        Write-Fail "Cannot set execution policy. Run as Administrator:`n  Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
    }
}

# ── Python ────────────────────────────────────────────────────────────────────
Write-Section "Python"

function Find-Python {
    foreach ($cmd in @("python3.13","python3.12","python3.11","python3.10","python3","python")) {
        try {
            $result = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($result) {
                $parts = $result.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) { return $cmd }
            }
        } catch {}
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Fail "Python 3.10+ not found.`n  Download from https://www.python.org/downloads/ (tick 'Add Python to PATH')"
}
$PyVer = & $Python -c "import sys; print(sys.version.split()[0])" 2>$null
Write-Ok "Python: $Python ($PyVer)"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Fail "git is required.`n  Install: winget install Git.Git  or  https://git-scm.com"
}

# ── Clone / update ────────────────────────────────────────────────────────────
Write-Section "Installing LLM Poison Detector"

if ($InstallDirRedirectedFrom) {
    Write-Info "Requested directory is not empty — using subdirectory: $InstallDir"
}

$RepoUrl = if ($env:LLP_REPO_URL) {
    $env:LLP_REPO_URL
} else {
    "https://github.com/Rebas9512/llm-poison-detector.git"
}

if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Existing installation found — updating..."
    git -C $InstallDir pull --ff-only --quiet
    Write-Ok "Updated to latest."
} else {
    Write-Info "Cloning into $InstallDir ..."
    git clone --depth=1 $RepoUrl $InstallDir --quiet
    Write-Ok "Cloned."
}

New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
@{
    install_method = "one_liner"
    install_dir = $InstallDir
} | ConvertTo-Json | Set-Content -Path $InstallMetaPath -Encoding UTF8

# ── Virtual environment ───────────────────────────────────────────────────────
Write-Section "Virtual environment"

$VenvDir    = Join-Path $InstallDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"
$LlpExe     = Join-Path $VenvDir "Scripts\llmpoison.exe"
$ScriptsDir = Join-Path $VenvDir "Scripts"

if (-not (Test-Path $VenvPython)) {
    Write-Info "Creating venv..."
    & $Python -m venv $VenvDir
    Write-Ok "Venv created."
} else {
    Write-Ok "Venv exists — reusing."
}

Write-Info "Upgrading pip..."
& $VenvPython -m pip install --upgrade pip --quiet

Write-Info "Installing dependencies..."
& $VenvPip install -r (Join-Path $InstallDir "requirements.txt") --quiet

# Register the 'llmpoison' console-scripts entry point.
& $VenvPip install -e $InstallDir --no-deps --quiet
Write-Ok "Dependencies installed."

# ── PATH ──────────────────────────────────────────────────────────────────────
Write-Section "PATH"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }

if ($userPath -notlike "*$ScriptsDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$ScriptsDir", "User")
    Write-Info "Added $ScriptsDir to user PATH (takes effect in new terminals)."
}

$env:Path = "$ScriptsDir;$env:Path"
Write-Ok "PATH updated."

# ── First-run check ───────────────────────────────────────────────────────────
Write-Section "Environment check"

if ($SkipSetup) {
    if ($NoSetup) {
        Write-Info "Skipping environment check (-NoSetup)."
    } else {
        Write-Info "Skipping environment check (LLP_NO_SETUP=1)."
    }
    Write-Info "Run later: cd $InstallDir && .venv\Scripts\python scripts\check_env.py"
    Write-Info "For unattended backbone download add LLP_AUTO_BACKBONE=1 when re-running."
} elseif (Test-Path $VenvPython) {
    if ($AutoBackbone -or $env:LLP_AUTO_BACKBONE -eq "1") {
        $env:LLP_AUTO_BACKBONE = "1"
    }
    $checkExit = 0
    try {
        & $VenvPython (Join-Path $InstallDir "scripts\check_env.py")
        $checkExit = $LASTEXITCODE
    } catch {
        $checkExit = 1
        Write-Warn "Environment check raised an exception: $_"
    }
    if ($checkExit -ne 0) {
        Write-Warn "Environment check exited with code $checkExit."
        Write-Warn "Re-run at any time: cd $InstallDir && .venv\Scripts\python scripts\check_env.py"
    } else {
        $ModelsSetup = $true
    }
} else {
    Write-Warn "Python not found in venv — run check_env.py manually after activation."
}

# ── Done ──────────────────────────────────────────────────────────────────────
Microsoft.PowerShell.Utility\Write-Host ""
Microsoft.PowerShell.Utility\Write-Host "${BOLD}  LLM Poison Detector installed!${NC}"
Microsoft.PowerShell.Utility\Write-Host ""

if ($env:Path -like "*$ScriptsDir*") {
    Microsoft.PowerShell.Utility\Write-Host "  ${GREEN}llmpoison${NC}    # start the dashboard (opens browser)"
    if (-not $ModelsSetup) {
        Microsoft.PowerShell.Utility\Write-Host ""
        Microsoft.PowerShell.Utility\Write-Host "  Backbone model not yet downloaded. Run first:"
        Microsoft.PowerShell.Utility\Write-Host "    ${MUTED}cd $InstallDir && .venv\Scripts\python scripts\download_default_backbone.py${NC}"
        Microsoft.PowerShell.Utility\Write-Host "  Or re-run check_env for an interactive prompt:"
        Microsoft.PowerShell.Utility\Write-Host "    ${MUTED}cd $InstallDir && .venv\Scripts\python scripts\check_env.py${NC}"
    }
} else {
    Microsoft.PowerShell.Utility\Write-Host "  Open a new terminal, then:"
    Microsoft.PowerShell.Utility\Write-Host "    ${GREEN}llmpoison${NC}    # start the dashboard"
    if (-not $ModelsSetup) {
        Microsoft.PowerShell.Utility\Write-Host ""
        Microsoft.PowerShell.Utility\Write-Host "  Backbone model not yet downloaded:"
        Microsoft.PowerShell.Utility\Write-Host "    ${MUTED}.venv\Scripts\python scripts\download_default_backbone.py${NC}"
    }
}
Microsoft.PowerShell.Utility\Write-Host ""
