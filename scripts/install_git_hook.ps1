Param(
    [string]$RepoRoot = "$(Split-Path -Path $PSScriptRoot -Parent)"
)

$scriptHook = Join-Path $PSScriptRoot "post-commit-hook.ps1"

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    Write-Error "This does not appear to be a git repository: $RepoRoot"
    exit 1
}

# Check if repository has a custom hooks path configured
try {
    $hooksConfig = git -C $RepoRoot config --get core.hooksPath 2>$null
} catch {
    $hooksConfig = $null
}

if ($hooksConfig) {
    # Resolve relative paths against repo root
    if (-not (Split-Path $hooksConfig -IsAbsolute)) {
        $hooksPathDir = Resolve-Path (Join-Path $RepoRoot $hooksConfig) -ErrorAction SilentlyContinue
        if ($hooksPathDir) { $hooksPathDir = $hooksPathDir.Path } else { $hooksPathDir = (Join-Path $RepoRoot $hooksConfig) }
    } else {
        $hooksPathDir = $hooksConfig
    }
} else {
    $hooksPathDir = Join-Path $RepoRoot ".git\hooks"
}

if (-not (Test-Path $hooksPathDir)) {
    try { New-Item -ItemType Directory -Path $hooksPathDir -Force | Out-Null } catch { }
}

$hookPath = Join-Path $hooksPathDir "post-commit"
Write-Host "Installing post-commit hook to $hookPath"

if (Test-Path $hookPath) {
    $bak = "$hookPath.bak.$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
    try {
        Copy-Item -Path $hookPath -Destination $bak -Force
        Write-Host "Existing hook backed up to: $bak"
    } catch { Write-Warning "Could not back up existing hook: $($_.Exception.Message)" }
}

# Write a lightweight wrapper that invokes the script hook via pwsh (preferred)
$content = "pwsh -NoProfile -ExecutionPolicy Bypass -File `"$scriptHook`"`n"
try {
    Set-Content -Path $hookPath -Value $content -Encoding UTF8 -Force
    Write-Host "Hook installed at: $hookPath"
    Write-Host "If you use a different PowerShell host on Windows, edit the hook to use 'powershell' instead of 'pwsh'."
} catch {
    Write-Error "Failed to install hook: $($_.Exception.Message)"
    exit 1
}

Write-Host "Done."
