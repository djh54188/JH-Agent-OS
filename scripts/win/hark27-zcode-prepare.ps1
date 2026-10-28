<#
Hark-27 ZCode bridge: Windows PREPARE / OFFLINE REPLAY (candidate; do not run before review).

What it does, in this order, and nothing else:
  1. Refuses unless -Commit is a 40-hex SHA and -Parent is a NEW (absent or empty) directory with
     no .env in it or any ancestor (exit 4 NEEDS_SETUP for the .env case).
  2. git init <Parent>\repo, fetch ONLY -Commit from the fixed GitHub URL, detached checkout,
     core.autocrlf=false. HEAD must equal -Commit; reviewed trees src and tests/fixtures/hark27
     and the replay driver blob must equal the pins below; worktree clean (ignored included).
  3. BEFORE any repo code runs: the bytes ON DISK of every file Python may import (src,
     scripts/win, tests/fixtures/hark27 recursively; repo top level) must equal the commit's
     blobs, file sets equal, no links / reparse points.
  4. If -ZcodeCjs is given: SHA-256 and size of that file vs the pin. The file is only hashed,
     never executed (exit 3 on mismatch).
  5. Runs the offline replay with a minimal environment: the real bridge against the FAKE host.
     No model, no ZCode process, no network, no user config, no key.
Exit codes: 0 OFFLINE_REPLAY_OK, 2 REFUSED, 3 HOST_PIN_MISMATCH, 4 NEEDS_SETUP,
5 REPLAY_NOT_PASSING, 1 internal. The driver's stdout is printed; its exit code is kept apart.
Real ZCode runs, the user's provider config, keys and localhost:8787 are NOT part of this script.
#>
param(
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$Parent,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$ZcodeCjs = '',
    [string]$GitHttpsProxy = '',
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
        'src'                   = '03929c3befd325c21579aeac9016a2c35484a19e'
        'tests/fixtures/hark27' = 'b87de7d59ef296dd09191b0e24a881ab9a53c0be'
    }
    driver           = 'scripts/win/hark27_zcode_replay.py'
    driver_blob      = '0fbce16f5bab7a212724ab9ef03a6add9ea64313'
    disk_dirs        = @('src', 'scripts/win', 'tests/fixtures/hark27')
    zcode_sha256     = 'A055234A6CA3DA0172616D3987CF3C617B6A26DEB1332D69035300C2A0AE0DE1'
    zcode_bytes      = 15243891
}
if ($TestConfig -ne '') {
    $over = Get-Content -LiteralPath $TestConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath (Join-Path ([string]$over.remote) 'hark27-test-fixture') -PathType Leaf)) {
        Write-Host 'HARK27-PS REFUSED: test config remote is not a marked local fixture'; exit 2
    }
    $Cfg.remote = [string]$over.remote
    $Cfg.allowed_protocol = 'file'
}
$KeepEnv = @('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP')

function Stop-With([int]$code, [string]$stage, [string]$why) {
    Write-Host ('HARK27-PS ' + $stage + ': ' + $why); exit $code
}
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
    $hdr = [Text.Encoding]::ASCII.GetBytes(('blob {0}' -f $b.Length) + [char]0)
    $h = [Security.Cryptography.SHA1]::Create()
    try {
        [void]$h.TransformBlock($hdr, 0, $hdr.Length, $null, 0)
        [void]$h.TransformFinalBlock($b, 0, $b.Length)
        return (-join ($h.Hash | ForEach-Object { $_.ToString('x2') }))
    } finally { $h.Dispose() }
}
function Join-Rel([string]$Repo, [string]$Rel) { return [IO.Path]::Combine([string[]](@($Repo) + ($Rel -split '/'))) }
function Get-TreeMap([string]$Repo, [string[]]$LsArgs) {
    $map = @{}
    foreach ($line in ((Invoke-Git $Repo (@('ls-tree') + $LsArgs)) -split "`n")) {
        if ($line -eq '') { continue }
        $tab = $line.IndexOf("`t")
        $meta = $line.Substring(0, $tab) -split ' '
        $path = $line.Substring($tab + 1)
        if ($path.StartsWith('"')) { throw 'unsupported path name in the commit' }
        $map[$path] = $meta
    }
    return $map
}
function Get-DiskFiles([string]$Repo, [string]$RelDir) {
    $found = @{}
    $stack = New-Object System.Collections.Stack
    $stack.Push($RelDir)
    while ($stack.Count -gt 0) {
        $cur = [string]$stack.Pop()
        $info = New-Object IO.DirectoryInfo (Join-Rel $Repo $cur)
        if (-not $info.Exists) { throw ($cur + ': missing') }
        if ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ($cur + ': link / reparse point') }
        foreach ($e in $info.GetFileSystemInfos()) {
            $rel = $cur + '/' + $e.Name
            if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ($rel + ': link / reparse point') }
            if ($e -is [IO.DirectoryInfo]) { $stack.Push($rel) } else { $found[$rel] = $true }
        }
    }
    return $found
}
function Assert-Source([string]$Repo, [string]$Want) {
    $head = Invoke-Git $Repo @('rev-parse', 'HEAD')
    if ($head -ne $Want) { throw 'repo HEAD is not the expected commit' }
    foreach ($k in $Cfg.trees.Keys) {
        if ((Invoke-Git $Repo @('rev-parse', ($Want + ':' + $k))) -ne $Cfg.trees[$k]) { throw ('tree ' + $k + ' is not the reviewed tree') }
    }
    if ((Invoke-Git $Repo @('rev-parse', ($Want + ':' + $Cfg.driver))) -ne $Cfg.driver_blob) { throw 'replay driver is not the reviewed blob' }
    if ((Invoke-Git $Repo @('status', '--porcelain', '--ignored', '--untracked-files=all')) -ne '') { throw 'worktree is not clean' }
}
function Assert-DiskBytes([string]$Repo, [string]$Want) {
    $expected = @{}; $disk = @{}
    foreach ($k in $Cfg.disk_dirs) {
        $m = Get-TreeMap $Repo @('-r', $Want, '--', $k)
        foreach ($p in $m.Keys) {
            if ($m[$p][1] -ne 'blob' -or @('100644', '100755') -notcontains $m[$p][0]) { throw ($p + ': not a plain file in the commit') }
            $expected[$p] = $m[$p][2]
        }
        foreach ($p in (Get-DiskFiles $Repo $k).Keys) { $disk[$p] = $true }
    }
    $top = Get-TreeMap $Repo @($Want)
    $topDisk = @((New-Object IO.DirectoryInfo $Repo).GetFileSystemInfos() | Where-Object { $_.Name -ne '.git' })
    if ($topDisk.Count -ne $top.Count) { throw 'repo top-level entries differ from the commit' }
    foreach ($e in $topDisk) {
        if (-not $top.ContainsKey($e.Name)) { throw 'repo top-level entries differ from the commit' }
        if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ($e.Name + ': link / reparse point') }
        $isBlob = $top[$e.Name][1] -eq 'blob'
        if ($isBlob -eq ($e -is [IO.DirectoryInfo])) { throw ($e.Name + ': top-level entry is not what the commit has') }
        if ($isBlob) { $expected[$e.Name] = $top[$e.Name][2]; $disk[$e.Name] = $true }
    }
    foreach ($p in $disk.Keys) { if (-not $expected.ContainsKey($p)) { throw ('extra file on disk: ' + $p) } }
    foreach ($p in $expected.Keys) {
        if (-not $disk.ContainsKey($p)) { throw ('file missing on disk: ' + $p) }
        if ((Get-GitBlobSha1 ([IO.File]::ReadAllBytes((Join-Rel $Repo $p)))) -ne $expected[$p]) { throw ('bytes on disk differ from the commit: ' + $p) }
    }
}
function Find-EnvFile([string]$Dir) {
    $cur = [IO.Path]::GetFullPath($Dir)
    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $cur '.env')) { return (Join-Path $cur '.env') }
        $up = [IO.Path]::GetDirectoryName($cur)
        if ([string]::IsNullOrEmpty($up) -or $up -eq $cur) { return $null }
        $cur = $up
    }
}

# 1. arguments and work root
if ($Commit -notmatch '^[0-9a-f]{40}$') { Stop-With 2 'REFUSED' '-Commit must be a 40-hex commit SHA' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { Stop-With 4 'NEEDS_SETUP' '-Python must be the full path of python.exe' }
$Parent = [IO.Path]::GetFullPath($Parent)
if (Test-Path -LiteralPath $Parent) {
    if (@(Get-ChildItem -LiteralPath $Parent -Force).Count -ne 0) { Stop-With 2 'REFUSED' '-Parent must be a new or empty directory' }
} else { New-Item -ItemType Directory -Path $Parent | Out-Null }
$envFile = Find-EnvFile $Parent
if ($envFile) { Stop-With 4 'NEEDS_SETUP' 'a .env file in the work root or an ancestor would be loaded by ZCode; not moved or deleted here' }

# 2-3. fixed source and disk bytes, before any repo code runs
$RepoDir = Join-Path $Parent 'repo'
try {
    New-Item -ItemType Directory -Path $RepoDir | Out-Null
    Invoke-Git $RepoDir @('init', '-q') | Out-Null
    Invoke-Git $RepoDir @('config', 'core.autocrlf', 'false') | Out-Null
    Invoke-Git $RepoDir @('remote', 'add', 'origin', $Cfg.remote) | Out-Null
    $fetch = @('fetch', '-q', '--no-tags', 'origin', $Commit)
    if ($GitHttpsProxy -ne '') { $fetch = @('-c', ('http.proxy=' + $GitHttpsProxy)) + $fetch }
    Invoke-Git $RepoDir $fetch | Out-Null
    Invoke-Git $RepoDir @('checkout', '-q', '--detach', $Commit) | Out-Null
    Assert-Source $RepoDir $Commit
    Assert-DiskBytes $RepoDir $Commit
} catch { Stop-With 2 'REFUSED' ('source check failed: ' + $_.Exception.Message) }

# 4. host build pin (hash only, never executed)
if ($ZcodeCjs -ne '') {
    if (-not (Test-Path -LiteralPath $ZcodeCjs -PathType Leaf)) { Stop-With 4 'NEEDS_SETUP' '-ZcodeCjs is not a file' }
    $len = (Get-Item -LiteralPath $ZcodeCjs).Length
    $hash = (Get-FileHash -LiteralPath $ZcodeCjs -Algorithm SHA256).Hash
    if ($hash -ne $Cfg.zcode_sha256 -or $len -ne $Cfg.zcode_bytes) { Stop-With 3 'HOST_PIN_MISMATCH' 'zcode.cjs is not the pinned build' }
}

# 5. offline replay, minimal environment, stdout and exit code kept apart
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $Python
$driverArgs = @('-I', '-B', (Join-Rel $RepoDir $Cfg.driver), '--work-root', (Join-Path $Parent 'replay'))
if ($ZcodeCjs -ne '') { $driverArgs += @('--zcode-cjs', $ZcodeCjs) }
$psi.Arguments = (($driverArgs | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' ')
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.WorkingDirectory = $Parent
$names = @($psi.EnvironmentVariables.Keys)
foreach ($n in $names) { if ($KeepEnv -notcontains $n.ToUpperInvariant()) { $psi.EnvironmentVariables.Remove($n) } }
$proc = [System.Diagnostics.Process]::Start($psi)
$errTask = $proc.StandardError.ReadToEndAsync()
$out = $proc.StandardOutput.ReadToEnd()
$proc.WaitForExit()
$null = $errTask.Result
$code = [int]$proc.ExitCode
Write-Host $out.TrimEnd()
if (@(0, 3, 4, 5) -notcontains $code) { $code = 1 }
Write-Host ('HARK27-PS exit ' + $code)
exit $code
