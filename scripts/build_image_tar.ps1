Param(
    [string]$ImageName = "personal-tracking",
    [string]$Tag = "auto",
    [switch]$MultiArch,
    [string]$OutDir = "dist",
    [string]$CopyToShare = "\\MEDIASERVER\Downloads\dockerconfig\personal-tracking\container_files"
)

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fullTag = "$ImageName:$Tag"
$tarFile = Join-Path $OutDir "$ImageName-$Tag-$timestamp.tar"

Write-Host "[hook] Building image $fullTag"

if ($MultiArch) {
    docker buildx build --platform linux/amd64,linux/arm64 -t $fullTag --load . || exit 1
} else {
    docker build -t $fullTag . || exit 1
}

Write-Host "[hook] Saving image to $tarFile"
docker save -o $tarFile $fullTag || exit 1

# Optionally maintain a 'latest.tar' symlink-like copy (Windows copy)
$latestFile = Join-Path $OutDir "$ImageName-latest.tar"
Copy-Item $tarFile $latestFile -Force
Write-Host "[hook] Created $tarFile and updated $latestFile"

# Copy to network share if available
if ($CopyToShare) {
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