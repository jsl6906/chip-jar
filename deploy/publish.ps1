# Build the image on this PC and drop it on the MediaServer share; then run update_chip_jar.sh on the server.
param([string]$Share = "\\MediaServer\Docker\chip-jar")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$tag = "chip-jar:" + (Get-Date -Format "yyyyMMdd-HHmm")

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

# Copy with LF line endings so bash can run it
$script = (Get-Content "$PSScriptRoot\update_chip_jar.sh" -Raw) -replace "`r`n", "`n"
[IO.File]::WriteAllText("$Share\update_chip_jar.sh", $script)
Write-Host "Saved $tar. On the server run: bash /mnt/samsung_ssd/dockerconfig/chip-jar/update_chip_jar.sh"
