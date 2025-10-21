Param()

# Simplified: always build current project image and tag with git short SHA (or timestamp)
$ImageName = "personal-tracking"
$OutDir = "dist"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# Determine tag from git commit short SHA; fallback to timestamp
try {
    $tag = git rev-parse --short HEAD 2>$null
    if (-not $tag -or $LASTEXITCODE -ne 0) { throw "no-sha" }
    $Tag = $tag.Trim()
} catch {
    $Tag = Get-Date -Format "yyyyMMdd-HHmmss"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fullTag = "${ImageName}:$Tag"
$tarFile = Join-Path $OutDir "$ImageName-$Tag-$timestamp.tar"

Write-Host "[hook] Building image $fullTag"

docker build -t $fullTag .
if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed"; exit 1 }

try {
    $inspect = docker image inspect $fullTag 2>$null | ConvertFrom-Json
    if ($inspect) { Write-Host "[hook] Image ID: $($inspect[0].Id)" }
} catch { }

Write-Host "[hook] Saving image to $tarFile"
docker save -o $tarFile $fullTag
if ($LASTEXITCODE -ne 0) { Write-Error "docker save failed"; exit 1 }

Write-Host "[hook] Created $tarFile"

# Prune old tars beyond 10 (default)
$Keep = 10
if ($Keep -gt 0) {
    $pattern = "$ImageName-*.tar"
    $tars = Get-ChildItem -Path $OutDir -Filter $pattern | Sort-Object LastWriteTime -Descending
    $extra = $tars | Select-Object -Skip ($Keep)
    foreach ($f in $extra) {
        try {
            Remove-Item $f.FullName -Force
            Write-Host "[hook] Pruned old tar: $($f.Name)"
        } catch { Write-Warning "[hook] Failed to prune $($f.Name): $($_.Exception.Message)" }
    }
}

Write-Host "[hook] Done"