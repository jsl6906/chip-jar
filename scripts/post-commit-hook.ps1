Param()

# This script is intended to be invoked by the .git/hooks/post-commit hook
$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$buildScript = Join-Path $PSScriptRoot "build_image_tar.ps1"

Write-Host "[git-hook] Running build script: $buildScript"
try {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $buildScript
} catch {
    Write-Warning "[git-hook] Build script failed: $($_.Exception.Message)"
}
