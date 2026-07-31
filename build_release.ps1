[CmdletBinding()]
param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectRoot

$Version = (& $PythonExe -c "from version import __version__; print(__version__)").Trim()
if (-not $Version) {
    throw "Unable to read the release version."
}

$BundleName = "MAF-v$Version-windows-x64"
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$DistRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
$ReleaseRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "release"))
$StageRoot = [System.IO.Path]::GetFullPath((Join-Path $ReleaseRoot $BundleName))
$ArchivePath = [System.IO.Path]::GetFullPath((Join-Path $ReleaseRoot "$BundleName.zip"))
$ChecksumPath = "$ArchivePath.sha256"

foreach ($Path in @($BuildRoot, $DistRoot, $StageRoot)) {
    if (-not $Path.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $Path"
    }
}

foreach ($Path in @($BuildRoot, $DistRoot, $StageRoot)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}
foreach ($Path in @($ArchivePath, $ChecksumPath)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
}

New-Item -ItemType Directory -Path $BuildRoot, $DistRoot, $ReleaseRoot -Force | Out-Null

& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name MAF `
    --distpath $DistRoot `
    --workpath $BuildRoot `
    --specpath $BuildRoot `
    --collect-all pystray `
    --hidden-import winotify `
    maf.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$BuiltApp = Join-Path $DistRoot "MAF"
if (-not (Test-Path -LiteralPath (Join-Path $BuiltApp "MAF.exe"))) {
    throw "The expected MAF.exe was not produced."
}

New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
Copy-Item -Path (Join-Path $BuiltApp "*") -Destination $StageRoot -Recurse -Force
Copy-Item -LiteralPath "README.md", "LICENSE", "release_notes.md" -Destination $StageRoot

$ThirdPartyRoot = Join-Path $StageRoot "THIRD_PARTY_LICENSES"
& $PythonExe tools\collect_licenses.py $ThirdPartyRoot
if ($LASTEXITCODE -ne 0) {
    throw "Third-party license collection failed with exit code $LASTEXITCODE."
}

Compress-Archive -LiteralPath $StageRoot -DestinationPath $ArchivePath -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $BundleName.zip" | Set-Content -LiteralPath $ChecksumPath -Encoding ascii

Write-Host ""
Write-Host "Release created:"
Write-Host "  Folder:   $StageRoot"
Write-Host "  Archive:  $ArchivePath"
Write-Host "  SHA-256:  $Hash"
