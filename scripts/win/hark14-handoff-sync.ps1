#requires -Version 5.1
<#
Hark-dispatch-14: one-shot local -> GitHub handoff sync (Windows; run by the user).

Two targets only, repository fixed to djh54188/JH-Agent-OS:
  A. new ref refs/heads/win-evidence-exec09-20261023 -> exactly 750b81673ee5b3d68bcab6c2ac85d87a32b94d72
  B. new branch refs/heads/handoff-local-gemini-20261023: ONE archive commit on top of
     29e00201f85063f60cc23213262fc82cf430d7ee adding only the 82 manifest payload files
     (original bytes, under reports/) plus the root MANIFEST.json saved as
     reports/local-handoff-sync-20261023/MANIFEST.json.

Default = prepare and check only; nothing is pushed. Only -Publish pushes, and only when every
check passed. Never force-pushes, never touches the source trees (read-only git commands only),
never changes global git config, never reads credentials (git uses the machine's own auth).
All work happens in a NEW directory under -WorkRoot. Output: PASS/FAIL lines, result.json, log.

Exit codes: 0 published and re-verified from the remote | 10 prepared, all checks PASS, not
published | 1 a check failed, nothing published by this run | 2 publish attempted but the remote
does not show exactly the expected state (see result.json).
#>
[CmdletBinding()]
param(
    [string]$PackageDir = 'D:\JH-Agent OS\data\worktrees\n1-artifact-entry\data\workflow-transition-20261023\package',
    [string]$SourceRepo = 'D:\JH-Agent OS\data\worktrees\n1-artifact-entry',
    [string]$WorkRoot = 'D:\JH-Agent OS\data\hark14-handoff-sync',
    [switch]$Publish,
    # Test-only overrides. The production command passes neither.
    [string]$RemoteUrl = 'https://github.com/djh54188/JH-Agent-OS.git',
    [string]$TestConfig = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$ProductionRemote = 'https://github.com/djh54188/JH-Agent-OS.git'
$Cfg = @{
    base_sha         = '29e00201f85063f60cc23213262fc82cf430d7ee'
    evidence_sha     = '750b81673ee5b3d68bcab6c2ac85d87a32b94d72'
    manifest_sha256  = 'a471e86b2a6e75091a00a9076073fe072a0afb8e4e52a69140862ba1bb9465b6'
    payload_count    = 82
    payload_bytes    = 985657
    ref_a            = 'refs/heads/win-evidence-exec09-20261023'
    ref_b            = 'refs/heads/handoff-local-gemini-20261023'
    manifest_target  = 'reports/local-handoff-sync-20261023/MANIFEST.json'
    payload_prefix   = 'reports/'
    source_meta      = 'n1-artifact-entry 03d9a0224fd93917ed3a1b9d514e465dfc996825; exec08 fdec9c6876e5def7432fe5e8c1e9ec99f425431d (metadata only, not pushed)'
    commit_date      = '2026-10-23T00:00:00+08:00'
}
$IsTest = $false
if ($TestConfig -ne '') {
    $IsTest = $true
    $over = Get-Content -LiteralPath $TestConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $over.PSObject.Properties) { $Cfg[$p.Name] = $p.Value }
}

# ------------------------------------------------------------------ state / reporting
$script:Checks = New-Object System.Collections.ArrayList
$script:Failed = $false
$Result = [ordered]@{
    schema = 'hark14-handoff-sync-result/v1'; mode = $(if ($Publish) { 'publish' } else { 'prepare' })
    test_mode = $IsTest; remote = $RemoteUrl; package_dir = $PackageDir; source_repo = $SourceRepo
    started = (Get-Date).ToString('o'); powershell = $PSVersionTable.PSVersion.ToString()
    os = [Environment]::OSVersion.VersionString; git = ''; work_dir = ''; config = $Cfg
    checks = $script:Checks; planned = [ordered]@{}; push = $null; remote_after = $null; verdict = ''
}
$script:LogPath = $null

function Write-Log([string]$line) {
    Write-Host $line
    if ($script:LogPath) { [IO.File]::AppendAllText($script:LogPath, $line + "`n", (New-Object System.Text.UTF8Encoding($false))) }
}
function Add-Check([string]$name, [bool]$ok, [string]$detail) {
    [void]$script:Checks.Add([ordered]@{ name = $name; ok = $ok; detail = $detail })
    if (-not $ok) { $script:Failed = $true }
    $tag = 'FAIL'; if ($ok) { $tag = 'PASS' }
    Write-Log ("{0} {1} :: {2}" -f $tag, $name, $detail)
}
function Finish([string]$verdict, [int]$code) {
    $Result.verdict = $verdict
    $Result.finished = (Get-Date).ToString('o')
    $Result.exit_code = $code
    if ($Result.work_dir -ne '') {
        $json = $Result | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText((Join-Path $Result.work_dir 'result.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
    }
    Write-Log ("VERDICT {0} (exit {1})" -f $verdict, $code)
    exit $code
}

# ------------------------------------------------------------------ git helper (no global config)
$script:GitDir = $null
function Invoke-Git {
    param([string[]]$GitArgs, [string]$Dir = $script:GitDir, [switch]$AllowFail)
    $all = @('-c', 'core.quotepath=false', '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false')
    if ($Dir) { $all += @('--git-dir', $Dir) }
    $all += $GitArgs
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & git @all 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    $text = (@($out) | ForEach-Object { "$_" }) -join "`n"
    if ($script:LogPath) { [IO.File]::AppendAllText($script:LogPath, ("  git {0} -> {1}`n" -f ($GitArgs -join ' '), $code), (New-Object System.Text.UTF8Encoding($false))) }
    if ($code -ne 0 -and -not $AllowFail) { throw ("git {0} failed ({1}): {2}" -f ($GitArgs -join ' '), $code, $text) }
    return [pscustomobject]@{ Code = $code; Text = $text.Trim() }
}

# ------------------------------------------------------------------ byte helpers
function Get-Sha256Hex([byte[]]$b) {
    $h = [Security.Cryptography.SHA256]::Create(); try { return (-join ($h.ComputeHash($b) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}
function Get-GitBlobSha1([byte[]]$b) {
    $hdr = [Text.Encoding]::ASCII.GetBytes(('blob {0}' -f $b.Length)) + [byte[]](0)
    $h = [Security.Cryptography.SHA1]::Create(); try { return (-join ($h.ComputeHash([byte[]]($hdr + $b)) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
}

# ------------------------------------------------------------------ guards
function Test-PayloadPath([string]$p) {          # GUARD:path
    if ([string]::IsNullOrEmpty($p)) { return 'empty path' }
    if (-not $p.StartsWith($Cfg.payload_prefix)) { return 'not under ' + $Cfg.payload_prefix }
    if ($p.Contains('\') -or $p.Contains(':') -or $p.StartsWith('/')) { return 'backslash, drive/stream colon or absolute' }
    if ($p -match '[\x00-\x1f<>"|?*]') { return 'control or Windows-invalid character' }
    foreach ($seg in $p.Split('/')) {
        if ($seg -eq '' -or $seg -eq '.' -or $seg -eq '..') { return 'empty, . or .. segment' }
        if ($seg.EndsWith('.') -or $seg.EndsWith(' ')) { return 'segment ends with dot or space' }
        if ($seg -match '^(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$') { return 'reserved device name' }
        if ($seg -ieq '.git') { return '.git segment' }
    }
    if ($p -ieq $Cfg.manifest_target) { return 'collides with the archived manifest path' }
    return $null
}
function Test-ManifestDigest([string]$actual) { return ($actual -eq $Cfg.manifest_sha256) }   # GUARD:digest
function Test-FileSetEqual($listed, $found) {   # GUARD:fileset
    $a = @($listed | Sort-Object); $b = @($found | Sort-Object)
    if ($a.Count -ne $b.Count) { return $false }
    for ($i = 0; $i -lt $a.Count; $i++) { if ($a[$i] -cne $b[$i]) { return $false } }
    return $true
}
function Test-NotReparse($item) { return -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) }   # GUARD:reparse
function Test-RemoteRefAllowed([string]$remoteSha, [string]$wanted) {   # GUARD:refconflict
    if ([string]::IsNullOrEmpty($remoteSha)) { return 'absent' }
    if ($remoteSha -eq $wanted) { return 'same' }
    return 'conflict'
}
function Test-RemoteBlob([string]$remoteBlob, [string]$expectedBlob) { return ($remoteBlob -eq $expectedBlob) }   # GUARD:postverify
function Test-RemoteRefExact([string]$remoteSha, [string]$expected) { return ((-not [string]::IsNullOrEmpty($remoteSha)) -and $remoteSha -eq $expected) }   # GUARD:postverify-ref

$script:PushAttempted = $false
trap {
    Write-Log ("ERROR unexpected: {0}" -f $_)
    if ($script:PushAttempted) { Finish 'PARTIAL_OR_MISMATCH' 2 } else { Finish 'FAIL' 1 }
}

# ================================================================== 0. setup
Write-Log ("Hark-dispatch-14 handoff sync :: mode={0} test={1}" -f $Result.mode, $IsTest)
if ($IsTest -and $RemoteUrl -eq $ProductionRemote) { Write-Log 'FAIL test config may not target the production remote'; exit 1 }
if (-not $IsTest -and $RemoteUrl -ne $ProductionRemote) { Write-Log 'FAIL a non-production remote requires -TestConfig'; exit 1 }
$gv = Invoke-Git -GitArgs @('--version') -Dir $null -AllowFail
$Result.git = $gv.Text
if ($gv.Code -ne 0) { Write-Log 'FAIL git not available'; exit 1 }
if ($gv.Text -match 'version (\d+)\.(\d+)') { if ([int]$Matches[1] -lt 2 -or ([int]$Matches[1] -eq 2 -and [int]$Matches[2] -lt 31)) { Write-Log 'FAIL git 2.31 or newer required'; exit 1 } }

if (-not (Test-Path -LiteralPath $WorkRoot)) { New-Item -ItemType Directory -Path $WorkRoot | Out-Null }
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss-fff')
$WorkDir = Join-Path $WorkRoot ('run-' + $stamp)
if (Test-Path -LiteralPath $WorkDir) { Write-Log 'FAIL work directory already exists; not reusing'; exit 1 }
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$Result.work_dir = $WorkDir
$script:LogPath = Join-Path $WorkDir 'sync-log.txt'
Write-Log ("work dir: {0}" -f $WorkDir)
Write-Log ("git: {0}; powershell: {1}" -f $gv.Text, $Result.powershell)

# ================================================================== 1. package
$pkgItem = $null
if (Test-Path -LiteralPath $PackageDir -PathType Container) { $pkgItem = Get-Item -LiteralPath $PackageDir -Force }
Add-Check 'package.exists' ($null -ne $pkgItem) $PackageDir
if ($null -eq $pkgItem) { Finish 'FAIL' 1 }
Add-Check 'package.root_not_reparse' (Test-NotReparse $pkgItem) $pkgItem.FullName
$root = $pkgItem.FullName.TrimEnd('\', '/')

# enumerate without following links
$found = New-Object System.Collections.ArrayList
$reparse = New-Object System.Collections.ArrayList
$stack = New-Object System.Collections.Stack; $stack.Push($root)
while ($stack.Count -gt 0) {
    $dir = $stack.Pop()
    foreach ($ch in @(Get-ChildItem -LiteralPath $dir -Force)) {
        $rel = $ch.FullName.Substring($root.Length + 1).Replace('\', '/')
        if (-not (Test-NotReparse $ch)) { [void]$reparse.Add($rel); continue }
        if ($ch.PSIsContainer) { $stack.Push($ch.FullName) } else { [void]$found.Add($rel) }
    }
}
Add-Check 'package.no_links_or_reparse_points' ($reparse.Count -eq 0) ($(if ($reparse.Count) { 'found: ' + (($reparse | Select-Object -First 5) -join ', ') } else { 'none' }))

$manPath = Join-Path $root 'MANIFEST.json'
$manBytes = $null
if (Test-Path -LiteralPath $manPath -PathType Leaf) { $manBytes = [IO.File]::ReadAllBytes($manPath) }
Add-Check 'manifest.present' ($null -ne $manBytes) 'MANIFEST.json at package root'
if ($null -eq $manBytes) { Finish 'FAIL' 1 }
$manSha = Get-Sha256Hex $manBytes
Add-Check 'manifest.sha256_fixed' (Test-ManifestDigest $manSha) ("actual {0}" -f $manSha)
if ($script:Failed) { Finish 'FAIL' 1 }     # never parse an unexpected manifest

# schema (assumed, see report): { "files": [ { "path": str, "sha256": hex64, "bytes": int }, ... ] }
$man = $null
try { $man = [Text.Encoding]::UTF8.GetString($manBytes) | ConvertFrom-Json } catch { }
$entries = @()
$schemaProblem = $null
if ($null -eq $man -or -not ($man -is [pscustomobject])) { $schemaProblem = 'root is not a JSON object' }
elseif (-not ($man.PSObject.Properties.Name -contains 'files')) { $schemaProblem = 'no "files"; root keys: ' + (($man.PSObject.Properties.Name) -join ',') }
else {
    $entries = @($man.files)
    foreach ($e in $entries) {
        if (-not ($e -is [pscustomobject])) { $schemaProblem = 'an entry is not an object'; break }
        $names = @($e.PSObject.Properties.Name)
        foreach ($k in 'path', 'sha256', 'bytes') { if (-not ($names -contains $k)) { $schemaProblem = 'entry keys: ' + ($names -join ','); break } }
        if ($schemaProblem) { break }
        if (-not ($e.path -is [string])) { $schemaProblem = 'path not a string'; break }
        if (-not ($e.sha256 -is [string]) -or $e.sha256 -cnotmatch '^[0-9a-f]{64}$') { $schemaProblem = 'sha256 not 64 lowercase hex'; break }
        if (-not (($e.bytes -is [int]) -or ($e.bytes -is [long])) -or $e.bytes -lt 0) { $schemaProblem = 'bytes not a non-negative integer'; break }
    }
}
Add-Check 'manifest.schema' ($null -eq $schemaProblem) ($(if ($schemaProblem) { $schemaProblem } else { 'files[].path/sha256/bytes' }))
if ($script:Failed) { Finish 'FAIL' 1 }

$listed = @($entries | ForEach-Object { $_.path })
$sum = 0L; foreach ($e in $entries) { $sum += [long]$e.bytes }
Add-Check 'manifest.count' ($entries.Count -eq [int]$Cfg.payload_count) ("{0} entries, expected {1}" -f $entries.Count, $Cfg.payload_count)
Add-Check 'manifest.total_bytes' ($sum -eq [long]$Cfg.payload_bytes) ("{0} bytes, expected {1}" -f $sum, $Cfg.payload_bytes)
$lower = @{}; $dup = $null
foreach ($p in $listed) { $k = $p.ToLowerInvariant(); if ($lower.ContainsKey($k)) { $dup = $p }; $lower[$k] = 1 }
Add-Check 'manifest.no_duplicate_paths_case_insensitive' ($null -eq $dup) ($(if ($dup) { $dup } else { 'none' }))
$badPaths = @()
foreach ($p in $listed) { $why = Test-PayloadPath $p; if ($why) { $badPaths += ("{0} ({1})" -f $p, $why) } }
Add-Check 'manifest.paths_whitelisted' ($badPaths.Count -eq 0) ($(if ($badPaths.Count) { ($badPaths | Select-Object -First 5) -join '; ' } else { 'all under ' + $Cfg.payload_prefix }))
$expectedSet = @($listed) + @('MANIFEST.json')
Add-Check 'package.file_set_equals_manifest' (Test-FileSetEqual $expectedSet @($found)) ("found {0} files, expected {1}" -f $found.Count, $expectedSet.Count)
if ($script:Failed) { Finish 'FAIL' 1 }

$files = New-Object System.Collections.ArrayList
$byteProblems = @()
foreach ($e in $entries) {
    $full = Join-Path $root ($e.path.Replace('/', [IO.Path]::DirectorySeparatorChar))
    $b = [IO.File]::ReadAllBytes($full)
    $s = Get-Sha256Hex $b
    if ($b.Length -ne [long]$e.bytes) { $byteProblems += ("{0}: size {1} != {2}" -f $e.path, $b.Length, $e.bytes) }
    elseif ($s -ne $e.sha256) { $byteProblems += ("{0}: sha256 mismatch" -f $e.path) }
    [void]$files.Add([ordered]@{ path = $e.path; full = $full; bytes = $b.Length; sha256 = $s; blob = (Get-GitBlobSha1 $b) })
}
Add-Check 'package.bytes_match_manifest' ($byteProblems.Count -eq 0) ($(if ($byteProblems.Count) { ($byteProblems | Select-Object -First 5) -join '; ' } else { '{0} files size+sha256 ok' -f $files.Count }))
[void]$files.Add([ordered]@{ path = $Cfg.manifest_target; full = $manPath; bytes = $manBytes.Length; sha256 = $manSha; blob = (Get-GitBlobSha1 $manBytes) })
if ($script:Failed) { Finish 'FAIL' 1 }

# ================================================================== 2. source of 750 (read-only)
$common = Invoke-Git -GitArgs @('-C', $SourceRepo, 'rev-parse', '--path-format=absolute', '--git-common-dir') -Dir $null -AllowFail
Add-Check 'source.repo_readable' ($common.Code -eq 0) $common.Text
if ($common.Code -ne 0) { Finish 'FAIL' 1 }
$srcGit = $common.Text
$t = Invoke-Git -GitArgs @('cat-file', '-t', $Cfg.evidence_sha) -Dir $srcGit -AllowFail
Add-Check 'source.evidence_commit_present' ($t.Code -eq 0 -and $t.Text -eq 'commit') ("{0}: {1}" -f $Cfg.evidence_sha, $t.Text)
if ($script:Failed) { Finish 'FAIL' 1 }
$par = Invoke-Git -GitArgs @('rev-list', '--parents', '-n', '1', $Cfg.evidence_sha) -Dir $srcGit
$parents = @($par.Text.Split(' ') | Select-Object -Skip 1)
Add-Check 'source.evidence_parent_is_base' ($parents.Count -eq 1 -and $parents[0] -eq $Cfg.base_sha) ('parents: ' + ($parents -join ','))
$conn = Invoke-Git -GitArgs @('rev-list', '--objects', $Cfg.evidence_sha, '--not', $Cfg.base_sha) -Dir $srcGit -AllowFail
Add-Check 'source.evidence_objects_complete' ($conn.Code -eq 0) ('objects reachable from 750 not in base enumerated')
if ($script:Failed) { Finish 'FAIL' 1 }

# ================================================================== 3. isolated repository
$iso = Join-Path $WorkDir 'sync.git'
Invoke-Git -GitArgs @('init', '--bare', '-q', $iso) -Dir $null | Out-Null
$script:GitDir = $iso
Invoke-Git -GitArgs @('config', 'core.autocrlf', 'false') | Out-Null
$alt = Join-Path (Join-Path $iso 'objects') 'info'
[IO.File]::WriteAllText((Join-Path $alt 'alternates'), ((Join-Path $srcGit 'objects') + "`n"), (New-Object System.Text.ASCIIEncoding))
Invoke-Git -GitArgs @('remote', 'add', 'origin', $RemoteUrl) | Out-Null
$f = Invoke-Git -GitArgs @('fetch', '-q', '--no-tags', 'origin', ($Cfg.base_sha + ':refs/hark14/base')) -AllowFail
Add-Check 'remote.base_fetchable' ($f.Code -eq 0) ($(if ($f.Code) { $f.Text } else { $Cfg.base_sha }))
if ($script:Failed) { Finish 'FAIL' 1 }

$baseNames = (Invoke-Git -GitArgs @('ls-tree', '-r', '--name-only', $Cfg.base_sha)).Text.Split("`n")
$baseLower = @{}; foreach ($n in $baseNames) { $baseLower[$n.ToLowerInvariant()] = $n }
$clash = @($files | Where-Object { $baseLower.ContainsKey($_.path.ToLowerInvariant()) } | ForEach-Object { $_.path })
Add-Check 'target.paths_new_in_base' ($clash.Count -eq 0) ($(if ($clash.Count) { 'exist in base: ' + (($clash | Select-Object -First 5) -join ', ') } else { 'none of {0} paths exist in base' -f $files.Count }))
if ($script:Failed) { Finish 'FAIL' 1 }

$index = Join-Path $WorkDir 'archive.index'
$env:GIT_INDEX_FILE = $index
try {
    Invoke-Git -GitArgs @('read-tree', $Cfg.base_sha) | Out-Null
    $blobBad = @()
    foreach ($x in $files) {
        $h = (Invoke-Git -GitArgs @('hash-object', '-w', '--no-filters', '--', $x.full)).Text
        if ($h -ne $x.blob) { $blobBad += $x.path }
        Invoke-Git -GitArgs @('update-index', '--add', '--cacheinfo', ('100644,{0},{1}' -f $h, $x.path)) | Out-Null
    }
    Add-Check 'archive.blobs_equal_original_bytes' ($blobBad.Count -eq 0) ($(if ($blobBad.Count) { ($blobBad | Select-Object -First 5) -join ', ' } else { 'git blob ids equal blob ids computed from file bytes' }))
    $tree = (Invoke-Git -GitArgs @('write-tree')).Text
} finally { Remove-Item Env:\GIT_INDEX_FILE -ErrorAction SilentlyContinue }
if ($script:Failed) { Finish 'FAIL' 1 }

$diff = @((Invoke-Git -GitArgs @('diff-tree', '-r', '--no-renames', '--name-status', $Cfg.base_sha, $tree)).Text.Split("`n") | Where-Object { $_ -ne '' })
$added = @($diff | Where-Object { $_.StartsWith("A`t") } | ForEach-Object { $_.Substring(2) })
$other = @($diff | Where-Object { -not $_.StartsWith("A`t") })
Add-Check 'archive.only_additions' ($other.Count -eq 0 -and (Test-FileSetEqual @($files | ForEach-Object { $_.path }) $added)) ("{0} added, {1} other" -f $added.Count, $other.Count)

$msg = "handoff(local): Hark-dispatch-14 archive of the frozen local Gemini handoff package`n`n" +
       ("manifest sha256: {0}`npayload: {1} files, {2} bytes, original bytes under {3}`n" -f $manSha, $entries.Count, $sum, $Cfg.payload_prefix) +
       ("root manifest archived at: {0}`nsource metadata: {1}`n" -f $Cfg.manifest_target, $Cfg.source_meta) +
       "History prompts inside are archive material, not execution authority.`n"
$msgFile = Join-Path $WorkDir 'commit-message.txt'
[IO.File]::WriteAllText($msgFile, $msg, (New-Object System.Text.UTF8Encoding($false)))
$env:GIT_AUTHOR_NAME = 'JH handoff sync'; $env:GIT_AUTHOR_EMAIL = 'handoff-sync@users.noreply.github.com'
$env:GIT_COMMITTER_NAME = $env:GIT_AUTHOR_NAME; $env:GIT_COMMITTER_EMAIL = $env:GIT_AUTHOR_EMAIL
$env:GIT_AUTHOR_DATE = $Cfg.commit_date; $env:GIT_COMMITTER_DATE = $Cfg.commit_date
try { $archive = (Invoke-Git -GitArgs @('commit-tree', $tree, '-p', $Cfg.base_sha, '-F', $msgFile)).Text }
finally { foreach ($v in 'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'GIT_AUTHOR_DATE', 'GIT_COMMITTER_DATE') { Remove-Item ("Env:\" + $v) -ErrorAction SilentlyContinue } }
Invoke-Git -GitArgs @('update-ref', 'refs/hark14/archive', $archive) | Out-Null
$Result.planned = [ordered]@{ ref_a = $Cfg.ref_a; ref_a_target = $Cfg.evidence_sha; ref_b = $Cfg.ref_b; ref_b_target = $archive; archive_tree = $tree; files = $files.Count }
Write-Log ("archive commit {0} (tree {1}, parent {2})" -f $archive, $tree, $Cfg.base_sha)
$Result.files = @($files | ForEach-Object { [ordered]@{ path = $_.path; bytes = $_.bytes; sha256 = $_.sha256; git_blob = $_.blob } })

# ================================================================== 4. remote refs
$lr = Invoke-Git -GitArgs @('ls-remote', 'origin', $Cfg.ref_a, $Cfg.ref_b) -AllowFail
Add-Check 'remote.ls_remote' ($lr.Code -eq 0) ($(if ($lr.Code) { $lr.Text } else { 'ok' }))
if ($script:Failed) { Finish 'FAIL' 1 }
$remote = @{}
foreach ($l in $lr.Text.Split("`n")) { if ($l -match '^([0-9a-f]{40})\t(\S+)$') { $remote[$Matches[2]] = $Matches[1] } }
$stateA = Test-RemoteRefAllowed $remote[$Cfg.ref_a] $Cfg.evidence_sha
$stateB = Test-RemoteRefAllowed $remote[$Cfg.ref_b] $archive
Add-Check 'remote.ref_a_absent_or_same' ($stateA -ne 'conflict') ("{0}: {1} ({2})" -f $Cfg.ref_a, $stateA, $remote[$Cfg.ref_a])
Add-Check 'remote.ref_b_absent_or_same' ($stateB -ne 'conflict') ("{0}: {1} ({2})" -f $Cfg.ref_b, $stateB, $remote[$Cfg.ref_b])
$Result.remote_before = [ordered]@{ ref_a = $remote[$Cfg.ref_a]; ref_b = $remote[$Cfg.ref_b] }
if ($script:Failed) { Finish 'FAIL' 1 }

if (-not $Publish) {
    Write-Log 'NOT PUBLISHED: prepare mode. Remote sync has NOT been performed.'
    Finish 'PREPARED_NOT_PUBLISHED' 10
}

# ================================================================== 5. publish (no force, atomic)
$specs = @()
if ($stateA -eq 'absent') { $specs += ('{0}:{1}' -f $Cfg.evidence_sha, $Cfg.ref_a) }
if ($stateB -eq 'absent') { $specs += ('{0}:{1}' -f $archive, $Cfg.ref_b) }
if ($specs.Count -gt 0) {
    $script:PushAttempted = $true
    $push = Invoke-Git -GitArgs (@('push', '--atomic', '--porcelain', 'origin') + $specs) -AllowFail
    $Result.push = [ordered]@{ refspecs = $specs; exit = $push.Code; output = $push.Text }
    Write-Log ("push exit {0}" -f $push.Code)
} else {
    $Result.push = [ordered]@{ refspecs = @(); exit = 0; output = 'both refs already at the expected commits; nothing pushed' }
    Write-Log 'both refs already at expected commits; idempotent re-check only'
}

# ================================================================== 6. re-read the remote, fresh repository, no alternates
$ver = Join-Path $WorkDir 'verify.git'
Invoke-Git -GitArgs @('init', '--bare', '-q', $ver) -Dir $null | Out-Null
$script:GitDir = $ver
Invoke-Git -GitArgs @('remote', 'add', 'origin', $RemoteUrl) | Out-Null
$va = Invoke-Git -GitArgs @('fetch', '-q', '--no-tags', 'origin', ('+{0}:refs/verify/a' -f $Cfg.ref_a)) -AllowFail
$vb = Invoke-Git -GitArgs @('fetch', '-q', '--no-tags', 'origin', ('+{0}:refs/verify/b' -f $Cfg.ref_b)) -AllowFail
$ra = $null; $rb = $null
if ($va.Code -eq 0) { $ra = (Invoke-Git -GitArgs @('rev-parse', 'refs/verify/a')).Text }
if ($vb.Code -eq 0) { $rb = (Invoke-Git -GitArgs @('rev-parse', 'refs/verify/b')).Text }
$Result.remote_after = [ordered]@{ ref_a = $ra; ref_b = $rb }
Add-Check 'after.ref_a_exact' (Test-RemoteRefExact $ra $Cfg.evidence_sha) ("remote {0} = {1}" -f $Cfg.ref_a, $ra)
Add-Check 'after.ref_b_exact' (Test-RemoteRefExact $rb $archive) ("remote {0} = {1}" -f $Cfg.ref_b, $rb)
if ($rb) {
    $p = (Invoke-Git -GitArgs @('rev-list', '--parents', '-n', '1', $rb)).Text.Split(' ')
    Add-Check 'after.ref_b_parent_is_base' ($p.Count -eq 2 -and $p[1] -eq $Cfg.base_sha) ('parents: ' + (($p | Select-Object -Skip 1) -join ','))
    $d2 = @((Invoke-Git -GitArgs @('diff-tree', '-r', '--no-renames', '--name-status', $Cfg.base_sha, $rb) -AllowFail).Text.Split("`n") | Where-Object { $_ -ne '' })
    $add2 = @($d2 | Where-Object { $_.StartsWith("A`t") } | ForEach-Object { $_.Substring(2) })
    Add-Check 'after.ref_b_only_listed_additions' (($d2.Count -eq $add2.Count) -and (Test-FileSetEqual @($files | ForEach-Object { $_.path }) $add2)) ("{0} changes" -f $d2.Count)
    $bad = @()
    foreach ($x in $files) {
        $lt = (Invoke-Git -GitArgs @('ls-tree', $rb, '--', $x.path) -AllowFail).Text
        $rblob = $null; if ($lt -match '^100644 blob ([0-9a-f]{40})\t') { $rblob = $Matches[1] }
        $now = Get-GitBlobSha1 ([IO.File]::ReadAllBytes($x.full))
        if (-not (Test-RemoteBlob $rblob $x.blob) -or $now -ne $x.blob) { $bad += $x.path }
    }
    Add-Check 'after.remote_blobs_equal_package_bytes' ($bad.Count -eq 0) ($(if ($bad.Count) { ($bad | Select-Object -First 5) -join ', ' } else { '{0} blobs re-read from the remote match the package bytes (re-hashed now)' -f $files.Count }))
}
if (-not $script:Failed) { Finish 'PUBLISHED_VERIFIED' 0 }
if ($null -eq $Result.push -or $Result.push.exit -ne 0) {
    $same = ($ra -eq $Result.remote_before.ref_a) -and ($rb -eq $Result.remote_before.ref_b)
    if ($same) { Write-Log 'push failed; remote refs unchanged from before this run'; Finish 'FAIL' 1 }
}
Finish 'PARTIAL_OR_MISMATCH' 2
