#!/usr/bin/env pwsh
<#!
 Local git hook to auto-build docker image tar after each commit.
 To enable:
   git config core.hooksPath .githooks
 Adjust variables below as needed.
!>

Param(
  [string]$ImageName = "personal-tracking",
  [string]$TagPrefix = "commit",
  [switch]$MultiArch,
  [int]$Keep = 10,
  [switch]$VerboseHook
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Allow opt-out via env var
if ($env:PT_SKIP_TAR -eq '1') {
  Write-Host "[post-commit] PT_SKIP_TAR=1 -> skipping image build"; exit 0
}

function Write-Detail($msg) { if ($VerboseHook) { Write-Host "[post-commit] $msg" } }

# Quick changed files list to optionally skip docs-only commits
$changed = git diff-tree --no-commit-id --name-only -r HEAD 2>$null | Sort-Object
if (-not $changed) { Write-Detail "No changed files list produced (maybe merge commit?). Proceeding." }
else {
  $nonDoc = $changed | Where-Object { $_ -notmatch '\.md$' -and $_ -notmatch '^docs/' }
  if (-not $nonDoc) {
    Write-Detail "Docs-only commit detected; skipping image build."; exit 0
  }
}

# Derive short commit hash
$gitHash = git rev-parse --short HEAD
if (-not $gitHash) { Write-Warning "Cannot obtain git hash."; exit 0 }

$tag = "$TagPrefix-$gitHash"
Write-Host "[post-commit] Building docker image tag: $tag (Keep last $Keep)"

$script = Join-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) '..' 'scripts' 'build_image_tar.ps1'
if (-not (Test-Path $script)) {
  Write-Warning "Build script not found: $script"; exit 0
}

try {
  $args = @('-ImageName', $ImageName, '-Tag', $tag, '-Keep', $Keep)
  if ($MultiArch) { $args += '-MultiArch' }
  powershell -NoProfile -ExecutionPolicy Bypass -File $script @args
}
catch {
  Write-Warning "[post-commit] Image build failed: $($_.Exception.Message)"
}

Write-Host "[post-commit] Done"