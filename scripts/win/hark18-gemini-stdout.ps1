#requires -Version 5.1
<#
Hark-dispatch-18 Windows cell, evidence gate rebuilt in Hark-dispatch-19 (run by the user, only
after the architect reviewed this script).

What it does (nothing else):
  1. Creates a NEW directory under -WorkRoot, `git init` there, fetches ONLY the fixed replay
     source commit f6bf47c from GitHub and checks it out with core.autocrlf=false (repo-local
     config only). No existing repository, worktree or global git config is changed.
  2. Verifies the checkout: HEAD = fixed commit, parent, clean status, fixed blob ids.
  3. Reads (read-only) the ORIGINAL node22-run1 stdout.jsonl and requires 3212 bytes / 4f8effca...4aa.
  4. Runs only tests.test_hark18_gemini_stdout (parser, fake-process and original-byte replay,
     guard weakening). No real Gemini CLI, no Node, no model, no credentials, no localhost proxy.
  5. REPLAY result is decided first (PASS / FAIL / INCOMPLETE) and is never the final verdict.
  6. EVIDENCE stage: copies the small evidence files into reports/hark18-gemini-stdout-20261024/
     windows/, commits them on a NEW local branch win-evidence-hark18-20261024 and then VERIFIES the
     commit independently: branch ref = HEAD = new commit; exactly one parent = the fixed commit;
     the diff against the fixed commit is exactly the expected added files; every committed blob
     equals the bytes recorded when the file was snapshotted; the worktree is clean. A git exit
     code of 0 or a printed SHA alone never counts as delivered.
  7. Only then the final verdict is computed, written ONCE to out\result.json, read back and checked,
     and printed. Before that, out\result.json says RUNNING (final=false). The push command is printed
     only for a verified evidence commit. Nothing is pushed, retried, rolled back or cleaned up.

Exit codes / final verdicts (terminal line, out\result.json and exit code always agree):
  0  PASS                    replay PASS and evidence commit verified
  1  FAIL                    replay failed, checkout/identity checks failed, or the script errored
  3  INCOMPLETE              original stdout missing: original-byte replay did not run (never a pass)
  4  EVIDENCE_NOT_DELIVERED  replay PASS, but the evidence was not committed or did not verify
  5  RESULT_NOT_RECORDED     the final out\result.json could not be written or read back
The evidence stage reached and its failure (if any) are recorded in out\result.json and the log
for every verdict.
#>
[CmdletBinding()]
param(
    [string]$OriginalStdout = 'D:\JH-Agent OS\data\gemini-node22-probe-20261023\evidence\node22-run1\bridge\runs\gemini-run-92dc87c4d59a\stdout.jsonl',
    [string]$WorkRoot = 'D:\JH-Agent OS\data\hark18-gemini-stdout',
    [string]$Python = 'python',
    # Test-only overrides. The production command passes none of them.
    [string]$TestConfig = ''
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
    git               = 'git'
    allowed_protocol  = 'https'
    inject            = ''
}
$IsTest = $false
if ($TestConfig -ne '') {
    $IsTest = $true
    $over = Get-Content -LiteralPath $TestConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $over.PSObject.Properties) { $Cfg[$p.Name] = $p.Value }
    # A test remote must be a local fixture directory carrying the marker file; never a network URL.
    if (-not (Test-Path -LiteralPath (Join-Path ([string]$Cfg.remote) 'hark19-test-fixture') -PathType Leaf)) {
        throw 'test mode: remote is not a marked local test fixture'
    }
    $Cfg.allowed_protocol = 'file'
}

$script:Checks = New-Object System.Collections.ArrayList
$script:Failed = $false
$script:Incomplete = $false
$script:LogPath = $null
$script:OutDir = $null
$Evidence = [ordered]@{ status = 'not_started'; stage = 'not_started'; failed_stage = $null; error = $null
                        commit = $null; parent = $null; branch = $null; files = @(); verified = $false }
$Result = [ordered]@{
    schema = 'hark18-gemini-stdout-windows/v2'; final = $false; verdict = 'RUNNING'; exit_code = $null
    started = (Get-Date).ToString('o'); test_mode = $IsTest
    powershell = $PSVersionTable.PSVersion.ToString(); os = [Environment]::OSVersion.VersionString
    git = ''; python = ''; work_dir = ''; config = $Cfg; original = $null; tests = $null
    replay = [ordered]@{ status = 'not_started'; completed = $false }
    evidence = $Evidence; real_cli_processes = 0; model_calls = 0; checks = $script:Checks
}

function Write-Log([string]$m) {
    Write-Host $m
    if ($script:LogPath) { try { [IO.File]::AppendAllText($script:LogPath, $m + "`n", $Utf8) } catch { } }
}
function Add-Check([string]$name, [bool]$ok, [string]$detail) {
    [void]$script:Checks.Add([ordered]@{ name = $name; ok = $ok; detail = $detail })
    if (-not $ok) { $script:Failed = $true }
    Write-Log ('{0} {1} :: {2}' -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
}
function Invoke-Git([string[]]$GitArgs, [switch]$AllowFail) {
    $all = @('-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.quotepath=false',
             '-c', 'protocol.allow=never', '-c', ('protocol.{0}.allow=always' -f $Cfg.allowed_protocol),
             '-C', $script:Repo) + $GitArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & $Cfg.git @all 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    $text = ((@($out) | ForEach-Object { "$_" }) -join "`n").Trim()
    if ($script:LogPath) { try { [IO.File]::AppendAllText($script:LogPath, ("  git {0} -> {1}`n" -f ($GitArgs -join ' '), $code), $Utf8) } catch { } }
    if ($code -ne 0 -and -not $AllowFail) { throw ("git {0} failed ({1}): {2}" -f ($GitArgs -join ' '), $code, $text) }
    return [pscustomobject]@{ Code = $code; Text = $text }
}
function Get-Sha256Hex([byte[]]$b) {
    $h = [Security.Cryptography.SHA256]::Create(); try { return (-join ($h.ComputeHash($b) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Get-GitBlobSha1([byte[]]$b) {
    $hdr = [Text.Encoding]::ASCII.GetBytes(('blob {0}' -f $b.Length)) + [byte[]](0)
    $h = [Security.Cryptography.SHA1]::Create(); try { return (-join ($h.ComputeHash([byte[]]($hdr + $b)) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Test-Inject([string]$stage) {               # test mode only: simulate a non-git failure
    if ($IsTest -and ([string]$Cfg.inject) -eq $stage) { throw ('injected failure at ' + $stage) }
}
function Set-EvidenceStage([string]$s) { $Evidence.stage = $s; Write-Log ('EVIDENCE stage ' + $s) }

# ------------------------------------------------------------------ new isolated directory
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$WorkDir = Join-Path $WorkRoot ('run-' + $stamp)
if (Test-Path -LiteralPath $WorkDir) { throw "work dir already exists: $WorkDir" }
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$script:Repo = Join-Path $WorkDir 'repo'
$script:OutDir = Join-Path $WorkDir 'out'
New-Item -ItemType Directory -Path $script:Repo, $script:OutDir | Out-Null
$script:LogPath = Join-Path $script:OutDir 'run-log.txt'
$ResultPath = Join-Path $script:OutDir 'result.json'
$Result.work_dir = $WorkDir
# Placeholder: until the very end, the result file says RUNNING / final=false, never PASS.
[IO.File]::WriteAllText($ResultPath, ($Result | ConvertTo-Json -Depth 8), $Utf8)
Write-Log ('Hark-dispatch-18 Windows cell (evidence gate v2) :: work dir {0}' -f $WorkDir)

# ================================================================== REPLAY
try {
    $Result.git = ((& $Cfg.git --version) | Out-String).Trim()
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
        Add-Check 'original.digest' (($orig.bytes -eq [int]$Cfg.original_bytes) -and ($orig.sha256 -eq $Cfg.original_sha256)) ("{0} bytes sha256 {1}" -f $orig.bytes, $orig.sha256)
    }

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
    Add-Check 'tests.weakening' (($weakRows.Count -eq [int]$Cfg.weakening_guards) -and ($ctlPass -eq [int]$Cfg.weakening_guards) -and ($detected -eq [int]$Cfg.weakening_guards)) ("rows {0} control_pass {1} detected {2}" -f $weakRows.Count, $ctlPass, $detected)
    $Result.replay.completed = $true
} catch {
    Add-Check 'replay.exception' $false ($_.Exception.Message)
}
$replayStatus = 'PASS'
if ($script:Failed) { $replayStatus = 'FAIL' } elseif ($script:Incomplete) { $replayStatus = 'INCOMPLETE' }
$Result.replay.status = $replayStatus
Write-Log ('REPLAY {0} (not the final verdict)' -f $replayStatus)

# ================================================================== EVIDENCE
$PushLine = $null
try {
    Set-EvidenceStage 'identity_before'
    $head = (Invoke-Git @('rev-parse', '--verify', 'HEAD^{commit}')).Text
    if ($head -ne $Cfg.candidate) { throw ('HEAD {0} is not the fixed replay source {1}' -f $head, $Cfg.candidate) }
    $dirty = (Invoke-Git @('status', '--porcelain')).Text
    if ($dirty -ne '') { throw ('worktree not clean before evidence: ' + $dirty) }

    Set-EvidenceStage 'prepare_dir'
    Test-Inject 'prepare_dir'
    $dest = Join-Path $script:Repo ($Cfg.evidence_dir -replace '/', [IO.Path]::DirectorySeparatorChar)
    if (Test-Path -LiteralPath $dest) { throw ('evidence dir already exists: ' + $dest) }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    Set-EvidenceStage 'snapshot'
    $replayRecord = [ordered]@{ schema = 'hark18-gemini-stdout-replay/v1'; final = $false
        note = 'replay stage only; the final verdict and the evidence commit identity are recorded outside this commit (out\result.json)'
        replay = $Result.replay; candidate = $Cfg.candidate; original = $Result.original; tests = $Result.tests
        python = $Result.python; git = $Result.git; powershell = $Result.powershell; os = $Result.os
        test_mode = $IsTest; checks = $script:Checks }
    [IO.File]::WriteAllText((Join-Path $script:OutDir 'replay-result.json'), ($replayRecord | ConvertTo-Json -Depth 8), $Utf8)
    Write-Log 'EVIDENCE snapshot of run-log follows this line'
    $names = @('replay-result.json', 'run-log.txt')
    foreach ($f in @('hark18-tests.txt', 'weakening.txt')) { if (Test-Path -LiteralPath (Join-Path $script:OutDir $f)) { $names += $f } }
    if ($replayStatus -eq 'PASS' -and $names.Count -ne 4) { throw ('replay PASS but evidence files missing: have ' + ($names -join ',')) }
    $expected = [ordered]@{}
    foreach ($f in $names) {
        Test-Inject 'copy'
        $bytes = [IO.File]::ReadAllBytes((Join-Path $script:OutDir $f))
        [IO.File]::WriteAllBytes((Join-Path $dest $f), $bytes)
        $expected[$Cfg.evidence_dir + '/' + $f] = [ordered]@{ bytes = $bytes.Length; sha256 = Get-Sha256Hex $bytes; blob = Get-GitBlobSha1 $bytes }
    }
    $Evidence.files = @($expected.Keys | ForEach-Object { [ordered]@{ path = $_; bytes = $expected[$_].bytes; sha256 = $expected[$_].sha256; blob = $expected[$_].blob } })

    Set-EvidenceStage 'branch'
    Invoke-Git @('checkout', '-q', '-b', $Cfg.evidence_branch) | Out-Null
    Set-EvidenceStage 'add'
    Invoke-Git @('add', '--', $Cfg.evidence_dir) | Out-Null
    Set-EvidenceStage 'commit'
    Invoke-Git @('-c', 'user.name=JH Windows cell', '-c', 'user.email=jh-windows-cell@invalid', 'commit', '-q',
                 '-m', ('evidence(win): Hark-dispatch-18 stdout preamble replay {0} on {1}' -f $replayStatus, $Cfg.candidate)) | Out-Null

    # ---------------- independent verification of what was actually committed
    Set-EvidenceStage 'verify_identity'
    $ev = (Invoke-Git @('rev-parse', '--verify', 'HEAD^{commit}')).Text
    $branchSha = (Invoke-Git @('rev-parse', '--verify', ('refs/heads/' + $Cfg.evidence_branch + '^{commit}'))).Text
    $sym = (Invoke-Git @('symbolic-ref', '-q', 'HEAD')).Text
    if ($ev -notmatch '^[0-9a-f]{40}$') { throw ('evidence HEAD is not a commit id: ' + $ev) }
    if ($ev -eq $Cfg.candidate) { throw 'no new commit: HEAD is still the replay source' }
    if ($branchSha -ne $ev) { throw ('branch {0} = {1}, HEAD = {2}' -f $Cfg.evidence_branch, $branchSha, $ev) }    # GATE:branch
    if ($sym -ne ('refs/heads/' + $Cfg.evidence_branch)) { throw ('HEAD is not on the evidence branch: ' + $sym) }
    Set-EvidenceStage 'verify_parent'
    $parents = @(((Invoke-Git @('rev-list', '--parents', '-n', '1', $ev)).Text -split '\s+') | Where-Object { $_ -ne '' })
    if (($parents.Count -ne 2) -or ($parents[1] -ne $Cfg.candidate)) { throw ('evidence parents: ' + ($parents -join ' ')) }   # GATE:parent
    Set-EvidenceStage 'verify_paths'
    $diff = @(((Invoke-Git @('diff', '--no-renames', '--name-status', $Cfg.candidate, $ev)).Text -split "`n") | Where-Object { $_ -ne '' })
    $want = @($expected.Keys | ForEach-Object { "A`t" + $_ } | Sort-Object)
    $got = @($diff | Sort-Object)
    if (($want -join '|') -ne ($got -join '|')) { throw ('evidence diff differs: ' + ($got -join '; ')) }   # GATE:paths
    Set-EvidenceStage 'verify_content'
    foreach ($p in $expected.Keys) {
        $blob = (Invoke-Git @('rev-parse', ($ev + ':' + $p))).Text
        if ($blob -ne $expected[$p].blob) { throw ('committed blob {0} for {1} != snapshot {2}' -f $blob, $p, $expected[$p].blob) }   # GATE:content
    }
    Set-EvidenceStage 'verify_clean'
    $dirty = (Invoke-Git @('status', '--porcelain', '--untracked-files=all')).Text
    if ($dirty -ne '') { throw ('worktree not clean after evidence commit: ' + $dirty) }   # GATE:clean
    $Evidence.commit = $ev; $Evidence.parent = $parents[1]; $Evidence.branch = $Cfg.evidence_branch
    $Evidence.verified = $true; $Evidence.status = 'verified'; Set-EvidenceStage 'done'
} catch {
    $Evidence.status = 'failed'; $Evidence.failed_stage = $Evidence.stage; $Evidence.error = $_.Exception.Message
    Write-Log ('EVIDENCE FAILED at {0} :: {1}' -f $Evidence.stage, $_.Exception.Message)
}

# ================================================================== FINAL (computed once, recorded once)
$verdict = 'FAIL'; $code = 1
if ($replayStatus -eq 'FAIL') { $verdict = 'FAIL'; $code = 1 }
elseif ($replayStatus -eq 'INCOMPLETE') { $verdict = 'INCOMPLETE'; $code = 3 }
elseif ($Evidence.verified -eq $true -and $Evidence.status -eq 'verified') { $verdict = 'PASS'; $code = 0 }   # GATE:final
else { $verdict = 'EVIDENCE_NOT_DELIVERED'; $code = 4 }
if ($Evidence.verified -eq $true) {   # GATE:push
    $PushLine = ('git -C "{0}" push {1} {2}:refs/heads/{2}' -f $script:Repo, $Cfg.remote, $Cfg.evidence_branch)
}
$Result.final = $true; $Result.verdict = $verdict; $Result.exit_code = $code; $Result.finished = (Get-Date).ToString('o')
$Result.push_command = $PushLine
try {
    Test-Inject 'result_write'
    $json = $Result | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($ResultPath, $json, $Utf8)
    $back = Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (($back.verdict -ne $verdict) -or ($back.exit_code -ne $code) -or ($back.final -ne $true)) { throw 'result.json read-back differs' }
} catch {
    Write-Log ('RESULT NOT RECORDED :: {0} (replay {1}, evidence {2}, would-be verdict {3})' -f $_.Exception.Message, $replayStatus, $Evidence.status, $verdict)
    try { [IO.File]::WriteAllText($ResultPath, '{"final": true, "verdict": "RESULT_NOT_RECORDED", "exit_code": 5}', $Utf8) } catch { }
    $verdict = 'RESULT_NOT_RECORDED'; $code = 5; $PushLine = $null
}
Write-Log ('EVIDENCE {0}{1}' -f $Evidence.status, $(if ($Evidence.verified) { (' commit {0} parent {1} branch {2} (local, NOT pushed)' -f $Evidence.commit, $Evidence.parent, $Evidence.branch) } elseif ($Evidence.failed_stage) { ' at ' + $Evidence.failed_stage } else { '' }))
if ($PushLine) { Write-Log ('to hand it over (after the architect read the verdict): ' + $PushLine) }
Write-Log ('VERDICT {0} (exit {1}) replay={2} evidence={3}' -f $verdict, $code, $replayStatus, $Evidence.status)
exit $code
