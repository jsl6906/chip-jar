# Build the image on this PC and drop it on the MediaServer share; then run update_chip_jar.sh on the server.
# Also stages the server-relevant keys from ../.env as env.update, which update_chip_jar.sh merges into the server .env.
#   -EnvOnly  stage env.update + update script without rebuilding the image
param([string]$Share = "\\MediaServer\Docker\chip-jar", [switch]$EnvOnly)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$tag = "chip-jar:" + (Get-Date -Format "yyyyMMdd-HHmm")

# Keys that only make sense on this PC (the container reaches Jellyfin at the compose default http://jellyfin:8096).
$localOnly = "JELLYFIN_URL", "LOG_FILE", "RELOAD", "HOST", "PORT"
$envLines = foreach ($line in [IO.File]::ReadAllLines("$root\.env")) {
    if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
    if ($localOnly -contains $Matches[1]) { continue }
    # docker compose interpolates $ inside env_file values
    if ($Matches[2].Contains('$')) { throw "$($Matches[1]) contains '$'; single-quote the value in .env first" }
    "$($Matches[1])=$($Matches[2].Trim())"
}

if (-not $EnvOnly) {
    docker build --platform linux/amd64 -t $tag $root
    if ($LASTEXITCODE) { throw "docker build failed" }

    New-Item -ItemType Directory -Force "$Share\container_files" | Out-Null
    $name = ($tag -replace ":", "-") + ".tar"
    # docker save straight to the SMB share can leave an empty file
    $local = Join-Path $env:TEMP $name
    docker save -o $local $tag
    if ($LASTEXITCODE) { throw "docker save failed" }
    $tar = "$Share\container_files\$name"
    Copy-Item $local $tar -Force
    if ((Get-Item $tar).Length -ne (Get-Item $local).Length) { throw "copy to $tar is incomplete" }
    Remove-Item $local
    Write-Host "Saved $tar"
}

# LF endings, no BOM; the server script deletes it after merging.
[IO.File]::WriteAllText("$Share\env.update", (($envLines -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))
Write-Host ("Staged env keys: " + (($envLines | ForEach-Object { ($_ -split '=', 2)[0] }) -join ", "))

# Copy with LF line endings so bash can run it
$script = (Get-Content "$PSScriptRoot\update_chip_jar.sh" -Raw) -replace "`r`n", "`n"
[IO.File]::WriteAllText("$Share\update_chip_jar.sh", $script)
Write-Host "On the server run: bash /mnt/samsung_ssd/dockerconfig/chip-jar/update_chip_jar.sh"
