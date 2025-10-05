Param(
    [string]$ImageName = "personal-tracking",
    [string]$Tag = "auto",
    [switch]$MultiArch,
    [string]$OutDir = "dist",
    [int]$Keep = 10,
    [string]$CopyToShare = "\\MEDIASERVER\Downloads\dockerconfig\personal-tracking\container_files",
    [switch]$SkipSave,
    [switch]$SkipShare
)

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fullTag = "$ImageName:$Tag"
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
    docker buildx build --platform linux/amd64,linux/arm64 -t $fullTag --load . || exit 1
} else {
    docker build -t $fullTag . || exit 1
}

# Show resulting image digest (if available)
try {
    $inspect = docker image inspect $fullTag 2>$null | ConvertFrom-Json
    if ($inspect) { Write-Host "[hook] Image ID: $($inspect[0].Id)" }
} catch { }

if (-not $SkipSave) {
    Write-Host "[hook] Saving image to $tarFile"
    docker save -o $tarFile $fullTag || exit 1

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
        try {
            if (-not (Test-Path $CopyToShare)) {
                Write-Warning "[hook] Network share path not found: $CopyToShare (skipping copy)"
            } else {
                $shareName = Split-Path $tarFile -Leaf
                $destTar = Join-Path $CopyToShare $shareName
                $destLatest = Join-Path $CopyToShare (Split-Path $latestFile -Leaf)
                Copy-Item $tarFile $destTar -Force
                Copy-Item $latestFile $destLatest -Force
                Write-Host "[hook] Copied image tar to $destTar and updated $destLatest"
            }
        } catch {
            Write-Warning "[hook] Failed to copy tar to share: $($_.Exception.Message)"
        }
    }
} else {
    Write-Host "[hook] SkipSave set -> not exporting tar"
}

Write-Host "[hook] Done"