#requires -Version 5.1
<#
Hark-dispatch-18: Windows cell for the Gemini CLI 0.65.0 stdout preamble change (run by the user,
only after the architect reviewed this script).

What it does (nothing else):
  1. Creates a NEW directory under -WorkRoot, `git init` there, fetches ONLY the fixed candidate
     commit from GitHub and checks it out with core.autocrlf=false (repo-local config only).
     No existing repository, worktree or global git config is read for writing or changed.
  2. Verifies the checkout: HEAD = candidate, clean status, fixed blob ids of the changed files.
  3. Reads (read-only) the ORIGINAL node22-run1 stdout.jsonl, records its size and SHA-256 and
     requires 3212 bytes / 4f8effca...4aa. It is never copied, modified or moved.
  4. Runs only tests.test_hark18_gemini_stdout (parser replay, fake-process replay, original-byte
     replay, guard weakening). No real Gemini CLI, no Node, no model, no network besides the fetch,
     no credentials, no user home, no localhost proxy.
  5. Judges PASS/FAIL itself and commits the small evidence (logs, result.json) to a NEW local
     branch win-evidence-hark18-20261024 in the new repository. It does NOT push; it prints the
     push command for the user.

Exit codes / verdicts:
  0  PASS        every check passed, including the original-byte replay
  1  FAIL        a check failed (test failure, wrong checkout, original bytes differ, ...)
  3  INCOMPLETE  the original stdout file is missing/unreadable: the original-byte replay did not
                 run, so the result is NOT a pass (constructed replays alone are not enough here)
#>
[CmdletBinding()]
param(
    [string]$OriginalStdout = 'D:\JH-Agent OS\data\gemini-node22-probe-20261023\evidence\node22-run1\bridge\runs\gemini-run-92dc87c4d59a\stdout.jsonl',
    [string]$WorkRoot = 'D:\JH-Agent OS\data\hark18-gemini-stdout',
    [string]$Python = 'python'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$Utf8 = New-Object System.Text.UTF8Encoding($false)

$Cfg = [ordered]@{
    remote            = 'https://github.com/djh54188/JH-Agent-OS.git'
    candidate         = 'f6bf47c59688c43d927dede01b59f772520815d8'
    candidate_parent  = 'e3b2017ad5f5b2c208ca5e286dc67e7073307f42'
    blobs             = [ordered]@{
        'src/agent_session_gemini.py'               = '31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38'
        'tests/test_hark18_gemini_stdout.py'        = '373459c4be11eaa1ca9e3212c505e8e8f110819e'
        'tests/fixtures/gemini_cli/fake_cli_h18.py' = '8ae04c94fa90e62e2557f2a7d7add8d6e68d4524'
    }
    original_bytes    = 3212
    original_sha256   = '4f8effca4110d539f64bf06a90cb830b593f706ec82a4e3a65a28c8a67ffa4aa'
    evidence_branch   = 'win-evidence-hark18-20261024'
    evidence_dir      = 'reports/hark18-gemini-stdout-20261024/windows'
    test_module       = 'tests.test_hark18_gemini_stdout'
    weakening_guards  = 8
}

$script:Checks = New-Object System.Collections.ArrayList
$script:Failed = $false
$script:Incomplete = $false
$Result = [ordered]@{
    schema = 'hark18-gemini-stdout-windows/v1'; started = (Get-Date).ToString('o')
    powershell = $PSVersionTable.PSVersion.ToString(); os = [Environment]::OSVersion.VersionString
    git = ''; python = ''; work_dir = ''; config = $Cfg; original = $null; tests = $null
    real_cli_processes = 0; model_calls = 0; checks = $script:Checks; verdict = ''
}

function Write-Log([string]$m) { Write-Host $m; if ($script:LogPath) { [IO.File]::AppendAllText($script:LogPath, $m + "`n", $Utf8) } }
function Add-Check([string]$name, [bool]$ok, [string]$detail) {
    [void]$script:Checks.Add([ordered]@{ name = $name; ok = $ok; detail = $detail })
    if (-not $ok) { $script:Failed = $true }
    Write-Log ('{0} {1} :: {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
}
function Invoke-Git([string[]]$GitArgs, [switch]$AllowFail) {
    $all = @('-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.quotepath=false',
             '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always', '-C', $script:Repo) + $GitArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & git @all 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    $text = ((@($out) | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($script:LogPath) { [IO.File]::AppendAllText($script:LogPath, ("  git {0} -> {1}`n" -f ($GitArgs -join ' '), $code), $Utf8) }
    if ($code -ne 0 -and -not $AllowFail) { throw ("git {0} failed ({1}): {2}" -f ($GitArgs -join ' '), $code, $text) }
    return [pscustomobject]@{ Code = $code; Text = $text }
}
function Get-Sha256Hex([byte[]]$b) {
    $h = [Security.Cryptography.SHA256]::Create(); try { return (-join ($h.ComputeHash($b) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Finish([string]$verdict, [int]$code) {
    $Result.verdict = $verdict; $Result.finished = (Get-Date).ToString('o')
    if ($script:OutDir) { [IO.File]::WriteAllText((Join-Path $script:OutDir 'result.json'), ($Result | ConvertTo-Json -Depth 8), $Utf8) }
    Write-Log ('VERDICT {0} (exit {1})' -f $verdict, $code)
    exit $code
}

# ------------------------------------------------------------------ new isolated directory
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$WorkDir = Join-Path $WorkRoot ('run-' + $stamp)
if (Test-Path -LiteralPath $WorkDir) { throw "work dir already exists: $WorkDir" }
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$script:Repo = Join-Path $WorkDir 'repo'
$script:OutDir = Join-Path $WorkDir 'out'
New-Item -ItemType Directory -Path $script:Repo, $script:OutDir | Out-Null
$script:LogPath = Join-Path $script:OutDir 'run-log.txt'
$Result.work_dir = $WorkDir
Write-Log ('Hark-dispatch-18 Windows cell :: work dir {0}' -f $WorkDir)
$Result.git = ((& git --version) | Out-String).Trim()

try {
    # -------------------------------------------------------------- checkout of the fixed candidate
    Invoke-Git @('init', '-q') | Out-Null
    Invoke-Git @('config', '--local', 'core.autocrlf', 'false') | Out-Null
    Invoke-Git @('fetch', '-q', '--no-tags', $Cfg.remote, $Cfg.candidate) | Out-Null
    Invoke-Git @('checkout', '-q', '--detach', $Cfg.candidate) | Out-Null
    $head = (Invoke-Git @('rev-parse', 'HEAD')).Text
    Add-Check 'checkout.head' ($head -eq $Cfg.candidate) ("HEAD {0}" -f $head)
    $parent = (Invoke-Git @('rev-parse', 'HEAD^')).Text
    Add-Check 'checkout.parent' ($parent -eq $Cfg.candidate_parent) ("parent {0}" -f $parent)
    $status = (Invoke-Git @('status', '--porcelain')).Text
    Add-Check 'checkout.clean' ($status -eq '') ("status '{0}'" -f $status)
    foreach ($k in $Cfg.blobs.Keys) {
        $b = (Invoke-Git @('rev-parse', ('HEAD:' + $k))).Text
        $w = (Invoke-Git @('hash-object', '--no-filters', '--', $k)).Text
        Add-Check ('checkout.blob ' + $k) (($b -eq $Cfg.blobs[$k]) -and ($w -eq $b)) ("tree {0} worktree {1}" -f $b, $w)
    }
    if ($script:Failed) { throw 'checkout verification failed' }

    # -------------------------------------------------------------- original stdout (read-only)
    $orig = [ordered]@{ path = $OriginalStdout; present = $false; bytes = $null; sha256 = $null }
    if (Test-Path -LiteralPath $OriginalStdout -PathType Leaf) {
        $bytes = [IO.File]::ReadAllBytes($OriginalStdout)
        $orig.present = $true; $orig.bytes = $bytes.Length; $orig.sha256 = Get-Sha256Hex $bytes
        $orig.last_write_utc = (Get-Item -LiteralPath $OriginalStdout).LastWriteTimeUtc.ToString('o')
    }
    $Result.original = $orig
    if (-not $orig.present) {
        $script:Incomplete = $true
        Write-Log ('INCOMPLETE original stdout not found: {0} (original-byte replay will not run)' -f $OriginalStdout)
    } else {
        Add-Check 'original.digest' (($orig.bytes -eq $Cfg.original_bytes) -and ($orig.sha256 -eq $Cfg.original_sha256)) ("{0} bytes sha256 {1}" -f $orig.bytes, $orig.sha256)
    }

    # -------------------------------------------------------------- tests (replay only)
    $Result.python = ((& $Python -c "import sys,platform;print(sys.version.split()[0], platform.platform())") | Out-String).Trim()
    $testLog = Join-Path $script:OutDir 'hark18-tests.txt'
    $weakLog = Join-Path $script:OutDir 'weakening.txt'
    $env:HARK18_WEAKENING_LOG = $weakLog
    if ($orig.present -and -not $script:Failed) { $env:HARK18_ORIGINAL_STDOUT = $OriginalStdout } else { Remove-Item Env:HARK18_ORIGINAL_STDOUT -ErrorAction SilentlyContinue }
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $script:Repo
    try { $out = & $Python -m unittest -v $Cfg.test_module 2>&1; $testCode = $LASTEXITCODE } finally { Pop-Location; $ErrorActionPreference = $prev }
    Remove-Item Env:HARK18_ORIGINAL_STDOUT, Env:HARK18_WEAKENING_LOG -ErrorAction SilentlyContinue
    $text = (@($out) | ForEach-Object { "$_" }) -join "`n"
    [IO.File]::WriteAllText($testLog, $text + "`n", $Utf8)
    $ran = 0; if ($text -match '(?m)^Ran (\d+) test') { $ran = [int]$Matches[1] }
    $skips = @([regex]::Matches($text, '(?m)^(test_\w+) \(([\w\.]+)\) \.\.\. skipped') | ForEach-Object { if ($_.Groups[2].Value.EndsWith('.' + $_.Groups[1].Value)) { $_.Groups[2].Value } else { $_.Groups[2].Value + '.' + $_.Groups[1].Value } })
    $origLine = [regex]::Match($text, '(?m)^test_original_bytes \([\w\.]+\) \.\.\. (\w+)')
    $origState = $(if ($origLine.Success) { $origLine.Groups[1].Value } else { 'absent' })
    $weakRows = @(); if (Test-Path -LiteralPath $weakLog) { $weakRows = @(Get-Content -LiteralPath $weakLog -Encoding UTF8 | Where-Object { $_ -ne '' }) }
    $ctlPass = @($weakRows | Where-Object { $_ -match "`tcontrol=pass`t" }).Count
    $detected = @($weakRows | Where-Object { $_ -match "`tweakened=detected`t" }).Count
    $Result.tests = [ordered]@{ exit = $testCode; ran = $ran; skipped = $skips; original_replay = $origState
                                weakening_rows = $weakRows.Count; control_pass = $ctlPass; detected = $detected }
    Add-Check 'tests.exit' ($testCode -eq 0) ("unittest exit {0}, ran {1}" -f $testCode, $ran)
    if ($orig.present) {
        Add-Check 'tests.original_replay' ($origState -eq 'ok') ("test_original_bytes: {0}" -f $origState)
        Add-Check 'tests.no_skips' ($skips.Count -eq 0) ("skipped: {0}" -f ($skips -join ', '))
    } else {
        $other = @($skips | Where-Object { $_ -notmatch 'TestOriginalRun1Replay' })
        Add-Check 'tests.no_other_skips' ($other.Count -eq 0) ("skipped besides original replay: {0}" -f ($other -join ', '))
    }
    Add-Check 'tests.weakening' (($weakRows.Count -eq $Cfg.weakening_guards) -and ($ctlPass -eq $Cfg.weakening_guards) -and ($detected -eq $Cfg.weakening_guards)) ("rows {0} control_pass {1} detected {2}" -f $weakRows.Count, $ctlPass, $detected)
} catch {
    Add-Check 'script.exception' $false ($_.Exception.Message)
}

# ------------------------------------------------------------------ verdict + local evidence commit
$verdict = 'PASS'; $code = 0
if ($script:Failed) { $verdict = 'FAIL'; $code = 1 } elseif ($script:Incomplete) { $verdict = 'INCOMPLETE'; $code = 3 }
$Result.verdict = $verdict; $Result.finished = (Get-Date).ToString('o')
[IO.File]::WriteAllText((Join-Path $script:OutDir 'result.json'), ($Result | ConvertTo-Json -Depth 8), $Utf8)
try {
    $head = (Invoke-Git @('rev-parse', 'HEAD') -AllowFail).Text
    if ($head -eq $Cfg.candidate) {
        $dest = Join-Path $script:Repo ($Cfg.evidence_dir -replace '/', '\')
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        foreach ($f in @('result.json', 'hark18-tests.txt', 'weakening.txt', 'run-log.txt')) {
            $src = Join-Path $script:OutDir $f
            if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $dest $f) }
        }
        Invoke-Git @('checkout', '-q', '-b', $Cfg.evidence_branch) | Out-Null
        Invoke-Git @('add', '--', $Cfg.evidence_dir) | Out-Null
        Invoke-Git @('-c', 'user.name=JH Windows cell', '-c', 'user.email=jh-windows-cell@invalid', 'commit', '-q',
                     '-m', ('evidence(win): Hark-dispatch-18 stdout preamble replay {0} on {1}' -f $verdict, $Cfg.candidate)) | Out-Null
        $ev = (Invoke-Git @('rev-parse', 'HEAD')).Text
        Write-Log ('evidence commit {0} on local branch {1} (parent {2}); NOT pushed' -f $ev, $Cfg.evidence_branch, $Cfg.candidate)
        Write-Log ('to hand it over (after reading the verdict): git -C "{0}" push {1} {2}:refs/heads/{2}' -f $script:Repo, $Cfg.remote, $Cfg.evidence_branch)
    } else {
        Write-Log 'no evidence commit: checkout is not the fixed candidate'
    }
} catch { Write-Log ('evidence commit failed: ' + $_.Exception.Message) }
Write-Log ('VERDICT {0} (exit {1})' -f $verdict, $code)
exit $code
