param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$backupRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'backup')).Path
if ($projectRoot -ne 'C:\UCL\CASA0016\FloodPred-Agent' -or $backupRoot -ne "$projectRoot\backup") {
    throw 'This cleanup is restricted to the inspected FloodPred-Agent backup directory.'
}
$renameMap = [ordered]@{
    'd1_d2_snapshot_2026-09-29' = '01-d1-d2-20260929'
    'd1_d7_snapshot_2026-09-30' = '02-d1-d7-20260930'
    'pre_p1_1_2026-10-01' = '03-d1-d10-20261001'
    'pre_question_graph_20261003_134605' = '04-p1-20261003'
    'pre_raw_hybrid_20261003_144025' = '05-question-graph-20261003'
}
$archiveNames = @(
    'd1_d3_snapshot_2026-09-29','d1_d4_snapshot_2026-09-29','d1_d5_snapshot_2026-09-29',
    'pre_caution_thinking_2026-09-30','pre_fuzzy_housemill_2026-09-30',
    'pre_housemill_context_2026-09-30','pre_llm_planner_2026-09-30',
    'pre_p1_2_p1_3_2026-10-02','pre_p1_3_partial_2026-10-02','pre_p1_graph_2026-10-02',
    'pre_thesis_router_ui_2026-09-30','pre_thinking_toggle_2026-09-30'
)
function Assert-Target([string]$path) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (!(Split-Path -Parent $resolved).Equals($backupRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target is not an immediate child of the intended backup directory: $resolved"
    }
    $item = Get-Item -LiteralPath $resolved
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused.' }
    return $resolved
}
foreach ($name in @($renameMap.Keys) + $archiveNames) { $null = Assert-Target (Join-Path $backupRoot $name) }
if (!$Execute) { [pscustomobject]@{Rename=$renameMap; Archive=$archiveNames; Snapshot='06-hybrid-20261003'}; return }

# Snapshot before changing any retained backup or active implementation.
$snapshot = Join-Path $backupRoot '06-hybrid-20261003'
if (Test-Path -LiteralPath $snapshot) { throw 'Snapshot already exists; refusing to overwrite.' }
$null = New-Item -ItemType Directory -Path $snapshot
$count = 0
foreach ($file in Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force) {
    $relative = $file.FullName.Substring($projectRoot.Length + 1)
    if ($relative -match '^(backup|logs|\.git|\.venv[^\\]*|tmp|temp)\\|(^|\\)(__pycache__|\.pytest_cache)\\' -or
        ($file.Name -like '.env*' -and $file.Name -ne '.env.example')) { continue }
    $destination = Join-Path $snapshot $relative
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) {
        throw "Snapshot hash mismatch: $relative"
    }
    $count++
}

# Archive all small granular snapshots, verify each ZIP entry's bytes first.
$archive = Join-Path $backupRoot 'archived-legacy-20261003.zip'
if (Test-Path -LiteralPath $archive) { throw 'Archive exists; refusing to overwrite.' }
$targets = @($archiveNames | ForEach-Object { Assert-Target (Join-Path $backupRoot $_) })
Compress-Archive -LiteralPath $targets -DestinationPath $archive -CompressionLevel Optimal
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
$verified = 0
try {
    foreach ($target in $targets) {
        foreach ($file in Get-ChildItem -LiteralPath $target -Recurse -File -Force) {
            $relative = $file.FullName.Substring($backupRoot.Length + 1).Replace('\','/')
            $entry = $zip.GetEntry($relative)
            if (!$entry) { throw "Missing archive entry: $relative" }
            $stream = $entry.Open()
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $digest = [Convert]::ToHexString($sha.ComputeHash($stream)) }
            finally { $stream.Dispose(); $sha.Dispose() }
            if ($digest -ne (Get-FileHash -LiteralPath $file.FullName).Hash) { throw "ZIP hash mismatch: $relative" }
            $verified++
        }
    }
} finally { $zip.Dispose() }
foreach ($target in $targets) {
    $validated = Assert-Target $target
    Remove-Item -LiteralPath $validated -Recurse -Force
}
foreach ($oldName in $renameMap.Keys) {
    $target = Assert-Target (Join-Path $backupRoot $oldName)
    $destination = Join-Path $backupRoot $renameMap[$oldName]
    if (Test-Path -LiteralPath $destination) { throw "Rename target exists: $destination" }
    Move-Item -LiteralPath $target -Destination $destination
}
$index = [ordered]@{Snapshot='06-hybrid-20261003'; SnapshotFiles=$count; Renamed=$renameMap;
    Archive='archived-legacy-20261003.zip'; ArchivedFolders=$archiveNames; VerifiedArchiveFiles=$verified;
    ImmutableData='source_snapshot_2026-09-29'; Note='Contents unchanged; recover granular folders by extracting the ZIP.'}
$index | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'index.json') -Encoding utf8
$index | ConvertTo-Json -Depth 5
