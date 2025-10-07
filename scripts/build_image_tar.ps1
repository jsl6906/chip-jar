Param(
    [string]$ImageName = "personal-tracking",
    [string]$Tag = "auto",
    [switch]$MultiArch,
    [string]$OutDir = "dist",
    [int]$Keep = 10,
    [string]$CopyToShare = "\\MEDIASERVER\Downloads\dockerconfig\personal-tracking\container_files",
    [switch]$SkipSave,
    [switch]$SkipShare,
    [int]$ShareRetries = 3,
    [int]$ShareRetryDelaySeconds = 4
)

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# Allow environment override for share path
if ($env:PT_SHARE_PATH -and -not $SkipShare) {
    $CopyToShare = $env:PT_SHARE_PATH
    Write-Host "[hook] Using share path override from PT_SHARE_PATH: $CopyToShare"
}

# Check Docker availability early
try {
    docker version --format '{{.Server.Version}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[hook] Docker daemon not available. Please start Docker Desktop."
        exit 1
    }
} catch {
    Write-Error "[hook] Docker not found. Please install Docker Desktop and add to PATH."
    exit 1
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
# Use braces to avoid scope-like parsing ($ImageName:$Tag would be invalid)
$fullTag = "${ImageName}:$Tag"
$tarFile = Join-Path $OutDir "$ImageName-$Tag-$timestamp.tar"

Write-Host "[hook] Building image $fullTag (MultiArch=$($MultiArch.IsPresent))"

if ($MultiArch) {
    # Ensure buildx builder exists
    $builderName = "pt-builder"
    $existing = docker buildx ls 2>$null | Select-String -Pattern "^$builderName" -SimpleMatch
    if (-not $existing) {
        Write-Host "[hook] Creating buildx builder $builderName"
        docker buildx create --name $builderName --use | Out-Null
    } else {
        docker buildx use $builderName | Out-Null
    }
    docker buildx build --platform linux/amd64,linux/arm64 -t $fullTag --load .
    if ($LASTEXITCODE -ne 0) { Write-Error "docker buildx failed"; exit 1 }
} else {
    docker build -t $fullTag .
    if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed"; exit 1 }
}

# Show resulting image digest (if available)
try {
    $inspect = docker image inspect $fullTag 2>$null | ConvertFrom-Json
    if ($inspect) { Write-Host "[hook] Image ID: $($inspect[0].Id)" }
} catch { }

if (-not $SkipSave) {
    Write-Host "[hook] Saving image to $tarFile"
    docker save -o $tarFile $fullTag
    if ($LASTEXITCODE -ne 0) { Write-Error "docker save failed"; exit 1 }

    # Maintain latest copy
    $latestFile = Join-Path $OutDir "$ImageName-latest.tar"
    Copy-Item $tarFile $latestFile -Force
    Write-Host "[hook] Created $tarFile and updated $latestFile"

    # Prune old tars beyond $Keep
    if ($Keep -gt 0) {
        $pattern = "$ImageName-*.tar"
        $tars = Get-ChildItem -Path $OutDir -Filter $pattern | Where-Object { $_.Name -notlike '*latest.tar' } | Sort-Object LastWriteTime -Descending
        $extra = $tars | Select-Object -Skip ($Keep)
        foreach ($f in $extra) {
            try {
                Remove-Item $f.FullName -Force
                Write-Host "[hook] Pruned old tar: $($f.Name)"
            } catch { Write-Warning "[hook] Failed to prune $($f.Name): $($_.Exception.Message)" }
        }
    }

    if (-not $SkipShare -and $CopyToShare) {
        # Attempt to create directory (will fail silently on permissions)
        if (-not (Test-Path $CopyToShare)) {
            try {
                New-Item -ItemType Directory -Path $CopyToShare -Force | Out-Null
                Write-Host "[hook] Created share directory: $CopyToShare"
            } catch { Write-Warning "[hook] Could not create share directory: $CopyToShare - $($_.Exception.Message)" }
        }
        if (-not (Test-Path $CopyToShare)) {
            Write-Warning "[hook] Share path still not accessible: $CopyToShare (skipping copy)"
        } else {
            $shareName = Split-Path $tarFile -Leaf
            $destTar = Join-Path $CopyToShare $shareName
            $destLatest = Join-Path $CopyToShare (Split-Path $latestFile -Leaf)
            $attempt = 0
            $success = $false
            while (-not $success -and $attempt -lt $ShareRetries) {
                $attempt++
                try {
                    Copy-Item $tarFile $destTar -Force
                    Copy-Item $latestFile $destLatest -Force
                    Write-Host "[hook] Copied image tar to $destTar and updated $destLatest (attempt $attempt)"
                    $success = $true
                } catch {
                    Write-Warning "[hook] Copy attempt $attempt failed: $($_.Exception.Message)"
                    if ($attempt -lt $ShareRetries) { Start-Sleep -Seconds $ShareRetryDelaySeconds }
                }
            }
            if (-not $success) { Write-Warning "[hook] All copy attempts failed ($ShareRetries attempts)." }
        }
    }
} else {
    Write-Host "[hook] SkipSave set -> not exporting tar"
}

Write-Host "[hook] Done"