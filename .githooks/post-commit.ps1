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
    [switch]$MultiArch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Derive short commit hash
$gitHash = git rev-parse --short HEAD
if (-not $gitHash) { Write-Warning "Cannot obtain git hash."; exit 0 }

$tag = "$TagPrefix-$gitHash"
Write-Host "[post-commit] Building docker image tag: $tag"

$script = Join-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) '..' 'scripts' 'build_image_tar.ps1'
if (-not (Test-Path $script)) {
    Write-Warning "Build script not found: $script"; exit 0
}

powershell -NoProfile -ExecutionPolicy Bypass -File $script -ImageName $ImageName -Tag $tag @(@{MultiArch=$true}[$MultiArch.IsPresent])

Write-Host "[post-commit] Done"