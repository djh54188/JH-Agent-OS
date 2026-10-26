#requires -Version 5.1
<#
Hark-dispatch-24: thin Windows entry for the FIRST real Gemini CLI task01 run (candidate).

Modes
  Prepare   (default; the only mode the user may run after the architect reviewed this script)
            1. Creates NEW directories under -Parent: repo-<stamp> (git init + fetch of ONLY -Commit
               from GitHub + detached checkout, core.autocrlf=false in that repo only) and the work
               root ctx-<stamp> (created by the Python driver).
            2. Verifies HEAD = -Commit and that this script's own bytes are the blob of
               scripts/win/hark24-gemini-task01.ps1 at -Commit.
            3. Runs `python -m scripts.gemini_task01_run.entry prepare`, which exports the fixed src/,
               writes a NON-sensitive context.json and prints PREPARED / NEEDS_SETUP / REFUSED.
            Prepare never reads a key value, never starts Node, the Gemini CLI, a model or a proxy,
            never dispatches. Static file checks are not a version or authentication check.
  RunReal   REVIEW TEMPLATE ONLY. Do NOT run it until the architect admits it and you accepted
            the real-call terms. Needs -Context, -AuthorizeRealModelCall and the exact
            -AcceptBoundary phrase. At most one submit; frozen on any dispatched/unknown run.
  Candidate After RunReal: commits the whitelisted small result files on a NEW local branch and
            verifies the commit. Never pushes.

The key is passed ONLY by variable NAME (-KeyEnvName); its value stays in this process's
environment and is handed to the host by the adapter's whitelist at RunReal. Never put a key in
-Endpoint, on the command line or in any file. Nothing global (PATH, git config, env) is changed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'RunReal', 'Candidate')] [string]$Mode = 'Prepare',
    [string]$Commit = '',
    [string]$Parent = 'D:\hark24-gemini-task01',
    [string]$Python = 'python',
    [string]$Node = '',
    [string]$CliJs = '',
    [string]$Endpoint = '',
    [string]$Model = '',
    [string]$KeyEnvName = '',
    [string[]]$ProxyEnvName = @(),
    [string]$Context = '',
    [switch]$AuthorizeRealModelCall,
    [string]$AcceptBoundary = '',
    [int]$CollectTimeout = 900
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$Remote = 'https://github.com/djh54188/JH-Agent-OS.git'
$Self = 'scripts/win/hark24-gemini-task01.ps1'
$env:PYTHONDONTWRITEBYTECODE = '1'      # this process only

function Invoke-Git([string]$Repo, [string[]]$GitArgs) {
    $all = @('-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'protocol.allow=never',
             '-c', 'protocol.https.allow=always', '-C', $Repo) + $GitArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & git @all 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    $text = ((@($out) | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($code -ne 0) { throw ("git {0} failed ({1}): {2}" -f ($GitArgs -join ' '), $code, $text) }
    return $text
}
function Get-GitBlobSha1([byte[]]$b) {
    $hdr = [Text.Encoding]::ASCII.GetBytes(('blob {0}' -f $b.Length)) + [byte[]](0)
    $h = [Security.Cryptography.SHA1]::Create()
    try { return (-join ($h.ComputeHash([byte[]]($hdr + $b)) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Invoke-Driver([string]$Repo, [string[]]$DriverArgs) {
    Push-Location -LiteralPath $Repo
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $Python -m scripts.gemini_task01_run.entry @DriverArgs
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
    } finally { Pop-Location }
    return $code
}

if ($Mode -eq 'Prepare') {
    if ($Commit -notmatch '^[0-9a-f]{40}$') { Write-Host 'REFUSED: -Commit must be the full 40-hex entry commit'; exit 2 }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $RepoDir = Join-Path $Parent ('repo-' + $stamp)
    $WorkRoot = Join-Path $Parent ('ctx-' + $stamp)
    if ((Test-Path -LiteralPath $RepoDir) -or (Test-Path -LiteralPath $WorkRoot)) { Write-Host 'REFUSED: target directories exist'; exit 2 }
    New-Item -ItemType Directory -Path $RepoDir | Out-Null
    try {
        Invoke-Git $RepoDir @('init', '-q') | Out-Null
        Invoke-Git $RepoDir @('config', '--local', 'core.autocrlf', 'false') | Out-Null
        Invoke-Git $RepoDir @('fetch', '-q', '--no-tags', $Remote, $Commit) | Out-Null
        Invoke-Git $RepoDir @('checkout', '-q', '--detach', $Commit) | Out-Null
        $head = Invoke-Git $RepoDir @('rev-parse', 'HEAD')
        if ($head -ne $Commit) { throw "HEAD $head is not $Commit" }
        $selfBlob = Get-GitBlobSha1 ([IO.File]::ReadAllBytes($PSCommandPath))
        $repoBlob = Invoke-Git $RepoDir @('rev-parse', ($Commit + ':' + $Self))
        if ($selfBlob -ne $repoBlob) { throw 'this script is not the one at -Commit' }
    } catch { Write-Host ('REFUSED: ' + $_.Exception.Message); exit 2 }
    $a = @('prepare', '--work-root', $WorkRoot, '--repo', $RepoDir, '--entry-commit', $Commit)
    if ($Node) { $a += @('--node', $Node) }
    if ($CliJs) { $a += @('--cli-js', $CliJs) }
    if ($Endpoint) { $a += @('--endpoint', $Endpoint) }
    if ($Model) { $a += @('--model', $Model) }
    if ($KeyEnvName) { $a += @('--key-env-name', $KeyEnvName) }
    foreach ($n in $ProxyEnvName) { $a += @('--proxy-env-name', $n) }
    $code = Invoke-Driver $RepoDir $a
    Write-Host ('Prepare finished (exit {0}). Context: {1}\context.json. Nothing was dispatched.' -f $code, $WorkRoot)
    exit $code
}

# RunReal / Candidate need an existing prepared context; its repo is read from context.json.
if (-not $Context -or -not (Test-Path -LiteralPath $Context -PathType Leaf)) { Write-Host 'REFUSED: -Context <work root>\context.json is required'; exit 2 }
$ctx = Get-Content -LiteralPath $Context -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Mode -eq 'RunReal') {
    if (-not $AuthorizeRealModelCall) { Write-Host 'REFUSED: -AuthorizeRealModelCall is required'; exit 2 }
    $code = Invoke-Driver $ctx.repo @('run-real', '--context', $Context, '--authorize-real-model-call',
                                      '--accept-boundary', $AcceptBoundary, '--collect-timeout', [string]$CollectTimeout)
    Write-Host ('RunReal finished (exit {0}). Send the result back before any push.' -f $code)
    exit $code
}
$code = Invoke-Driver $ctx.repo @('candidate', '--context', $Context)
exit $code
