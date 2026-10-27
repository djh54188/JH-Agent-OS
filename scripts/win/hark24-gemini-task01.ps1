#requires -Version 5.1
<#
Hark-dispatch-24/25: thin Windows entry for the FIRST real Gemini CLI task01 run (candidate).
DO NOT RUN ANY MODE before the architect has reviewed this script and admitted the command.

Modes
  Prepare   (default) Creates NEW <Parent>\repo-<stamp> (git init, origin = the fixed GitHub URL,
            fetch of ONLY -Commit, detached checkout, repo-local core.autocrlf=false) and lets the
            driver create <Parent>\ctx-<stamp>. Before ANY repo code runs it verifies: HEAD = -Commit,
            origin URL, this script's bytes = its blob at -Commit, the fixed trees below, and a clean
            worktree (ignored files included). The process environment is first reduced to a minimal
            whitelist (process scope only), so no key / endpoint / proxy variable reaches git or Python.
            Prints PREPARED / NEEDS_SETUP / REFUSED and the context sha256 to be reviewed.
  RunReal   REVIEW TEMPLATE ONLY (not admitted). Needs -Context, the REVIEWED -ContextSha256,
            -AuthorizeRealModelCall and the exact -AcceptBoundary phrase. At most one submit.
  Candidate Commits one whitelisted public summary on a NEW local branch; never pushes.

For RunReal / Candidate the repo is DERIVED from the context path (<Parent>\ctx-<stamp> ->
<Parent>\repo-<stamp>); no path from the JSON is used to choose code. The context file must match
-ContextSha256 and the repo must pass the same tree / clean checks before Python starts.
The driver's output is shown on the console; the exit code of this script is the driver's exit code
(a single integer), never its printed text.
#>
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'RunReal', 'Candidate')] [string]$Mode = 'Prepare',
    [string]$Commit = '',
    [string]$Parent = '',
    [string]$Python = 'python',
    [string]$Node = '',
    [string]$CliJs = '',
    [string]$Endpoint = '',
    [string]$Model = '',
    [string]$KeyEnvName = '',
    [string[]]$ProxyEnvName = @(),
    [string]$GitHttpsProxy = '',
    [string]$Context = '',
    [string]$ContextSha256 = '',
    [switch]$AuthorizeRealModelCall,
    [string]$AcceptBoundary = '',
    [int]$CollectTimeout = 900,
    # Test-only. The production command passes none of it.
    [string]$TestConfig = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$Cfg = @{
    remote           = 'https://github.com/djh54188/JH-Agent-OS.git'
    allowed_protocol = 'https'
    trees            = [ordered]@{
        'src'                       = 'eef830e74b3d12223de484b598563144f12208e3'
        'scripts/gemini_task01'     = 'a7cbc257b7a17ff9178f0fcc728f133d0f138d40'
        'scripts/gemini_task01_run' = '24280ad051befe05416693dc1ebecaa336fac106'
    }
}
if ($TestConfig -ne '') {
    $over = Get-Content -LiteralPath $TestConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath (Join-Path ([string]$over.remote) 'hark24-test-fixture') -PathType Leaf)) {
        Write-Host 'HARK24-PS REFUSED: test config remote is not a marked local fixture'; exit 2
    }
    $Cfg.remote = [string]$over.remote
    $Cfg.allowed_protocol = 'file'
    if ($over.PSObject.Properties.Name -contains 'trees') {
        foreach ($p in $over.trees.PSObject.Properties) { $Cfg.trees[$p.Name] = [string]$p.Value }
    }
}
$Self = 'scripts/win/hark24-gemini-task01.ps1'
$KeepEnv = @('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'LANG', 'LC_ALL')
$script:DriverExit = 9

function Stop-Refused([string]$why) { Write-Host ('HARK24-PS REFUSED: ' + $why); exit 2 }

function Invoke-Git([string]$Repo, [string[]]$GitArgs) {
    $all = @('-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.quotepath=false',
             '-c', 'protocol.allow=never', '-c', ('protocol.{0}.allow=always' -f $Cfg.allowed_protocol),
             '-C', $Repo) + $GitArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & git @all 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    $text = ((@($out) | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($code -ne 0) { throw ("git {0} failed ({1})" -f $GitArgs[0], $code) }
    return $text
}
function Get-GitBlobSha1([byte[]]$b) {
    $hdr = [Text.Encoding]::ASCII.GetBytes(('blob {0}' -f $b.Length)) + [byte[]](0)
    $h = [Security.Cryptography.SHA1]::Create()
    try { return (-join ($h.ComputeHash([byte[]]($hdr + $b)) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Get-FileSha256([string]$path) {
    $h = [Security.Cryptography.SHA256]::Create()
    try { return (-join ($h.ComputeHash([IO.File]::ReadAllBytes($path)) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Assert-Repo([string]$Repo, [string]$Want) {
    # Fixed source BEFORE any of its code runs: commit, fixed trees, clean (ignored files included).
    if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git'))) { throw 'repo is not a git checkout' }
    $head = Invoke-Git $Repo @('rev-parse', 'HEAD')
    if ($head -ne $Want) { throw 'repo HEAD is not the expected commit' }
    foreach ($k in $Cfg.trees.Keys) {
        $t = Invoke-Git $Repo @('rev-parse', ($Want + ':' + $k))
        if ($t -ne $Cfg.trees[$k]) { throw ('tree ' + $k + ' is not the reviewed tree') }
    }
    $st = Invoke-Git $Repo @('status', '--porcelain', '--ignored', '--untracked-files=all')
    if ($st -ne '') { throw 'repo worktree is not clean (ignored files included)' }
}
function Invoke-Driver([string]$Repo, [string[]]$DriverArgs) {
    # Output goes to the console (Out-Host), NOT to this function's pipeline; the exit code is kept apart.
    $entry = [IO.Path]::Combine($Repo, 'scripts', 'gemini_task01_run', 'entry.py')
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        & $Python -I -B $entry @DriverArgs | Out-Host
        $script:DriverExit = [int]$LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
}

if ($Mode -eq 'Prepare') {
    if ($Commit -notmatch '^[0-9a-f]{40}$') { Stop-Refused '-Commit must be the full 40-hex entry commit' }
    if ($Parent -eq '') { Stop-Refused '-Parent must be given explicitly (the reviewed command names it)' }
    if ($GitHttpsProxy -ne '' -and $GitHttpsProxy -notmatch '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$') {
        Stop-Refused '-GitHttpsProxy must be http(s)://host[:port] without credentials'
    }
    foreach ($e in @(Get-ChildItem Env:)) {                  # process scope only
        if ($KeepEnv -notcontains $e.Name.ToUpperInvariant()) { Remove-Item -LiteralPath ('Env:' + $e.Name) }
    }
    $Parent = [IO.Path]::GetFullPath($Parent)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $RepoDir = Join-Path $Parent ('repo-' + $stamp)
    $WorkRoot = Join-Path $Parent ('ctx-' + $stamp)
    if ((Test-Path -LiteralPath $RepoDir) -or (Test-Path -LiteralPath $WorkRoot)) { Stop-Refused 'target directories exist' }
    New-Item -ItemType Directory -Path $RepoDir -Force | Out-Null
    try {
        Invoke-Git $RepoDir @('init', '-q') | Out-Null
        Invoke-Git $RepoDir @('config', '--local', 'core.autocrlf', 'false') | Out-Null
        Invoke-Git $RepoDir @('remote', 'add', 'origin', $Cfg.remote) | Out-Null
        if ((Invoke-Git $RepoDir @('remote', 'get-url', 'origin')) -ne $Cfg.remote) { throw 'origin URL not set as expected' }
        $fetch = @('fetch', '-q', '--no-tags', 'origin', $Commit)
        if ($GitHttpsProxy -ne '') { $fetch = @('-c', ('http.proxy=' + $GitHttpsProxy)) + $fetch }
        Invoke-Git $RepoDir $fetch | Out-Null
        Invoke-Git $RepoDir @('checkout', '-q', '--detach', $Commit) | Out-Null
        $selfBlob = Get-GitBlobSha1 ([IO.File]::ReadAllBytes($PSCommandPath))
        if ($selfBlob -ne (Invoke-Git $RepoDir @('rev-parse', ($Commit + ':' + $Self)))) { throw 'this script is not the one at -Commit' }
        Assert-Repo $RepoDir $Commit
    } catch { Stop-Refused $_.Exception.Message }
    $a = @('prepare', '--work-root', $WorkRoot, '--entry-commit', $Commit)
    if ($Node) { $a += @('--node', $Node) }
    if ($CliJs) { $a += @('--cli-js', $CliJs) }
    if ($Endpoint) { $a += @('--endpoint', $Endpoint) }
    if ($Model) { $a += @('--model', $Model) }
    if ($KeyEnvName) { $a += @('--key-env-name', $KeyEnvName) }
    foreach ($n in $ProxyEnvName) { $a += @('--proxy-env-name', $n) }
    Invoke-Driver $RepoDir $a
    Write-Host ('HARK24-PS Prepare exit {0}; nothing was dispatched. Context: {1}' -f $script:DriverExit, (Join-Path $WorkRoot 'context.json'))
    exit $script:DriverExit
}

# ---- RunReal / Candidate: derive, bind to the reviewed digest, verify the repo, only then run it
if ($Context -eq '' -or $ContextSha256 -notmatch '^[0-9a-f]{64}$') { Stop-Refused '-Context and the reviewed 64-hex -ContextSha256 are required' }
try {
    $ctxPath = [IO.Path]::GetFullPath($Context)
    if ([IO.Path]::GetFileName($ctxPath) -ne 'context.json') { throw 'context must be <Parent>\ctx-<stamp>\context.json' }
    $WorkRoot = [IO.Path]::GetDirectoryName($ctxPath)
    $leaf = [IO.Path]::GetFileName($WorkRoot)
    if ($leaf -notmatch '^ctx-([0-9]{8}-[0-9]{6})$') { throw 'work root is not ctx-<stamp>' }
    $RepoDir = Join-Path ([IO.Path]::GetDirectoryName($WorkRoot)) ('repo-' + $Matches[1])
    if (-not (Test-Path -LiteralPath $ctxPath -PathType Leaf)) { throw 'context file missing' }
    if ((Get-FileSha256 $ctxPath) -ne $ContextSha256) { throw 'context bytes are not the reviewed context' }
    $ctx = [IO.File]::ReadAllText($ctxPath, (New-Object System.Text.UTF8Encoding($false))) | ConvertFrom-Json
    if ($null -eq $ctx -or -not ($ctx -is [pscustomobject]) -or -not ($ctx.PSObject.Properties.Name -contains 'entry_commit')) { throw 'context is not an object with entry_commit' }
    $want = [string]$ctx.entry_commit
    if ($want -notmatch '^[0-9a-f]{40}$') { throw 'entry_commit not 40-hex' }
    Assert-Repo $RepoDir $want
} catch { Stop-Refused $_.Exception.Message }

if ($Mode -eq 'RunReal') {
    if (-not $AuthorizeRealModelCall) { Stop-Refused '-AuthorizeRealModelCall is required' }
    Invoke-Driver $RepoDir @('run-real', '--context', $ctxPath, '--context-sha256', $ContextSha256,
                             '--authorize-real-model-call', '--accept-boundary', $AcceptBoundary,
                             '--collect-timeout', [string]$CollectTimeout)
    Write-Host ('HARK24-PS RunReal exit {0}. Send the result back before any push.' -f $script:DriverExit)
    exit $script:DriverExit
}
Invoke-Driver $RepoDir @('candidate', '--context', $ctxPath, '--context-sha256', $ContextSha256)
Write-Host ('HARK24-PS Candidate exit {0}; nothing pushed.' -f $script:DriverExit)
exit $script:DriverExit
