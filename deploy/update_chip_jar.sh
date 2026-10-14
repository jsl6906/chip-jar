#!/usr/bin/env bash
# Load the newest image tar from container_files/ and recreate the chip-jar container.
# Run on the server: bash /mnt/samsung_ssd/dockerconfig/chip-jar/update_chip_jar.sh
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TAR_DIR="$SCRIPT_DIR/container_files"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_UPDATE="$SCRIPT_DIR/env.update"

# Merge keys staged by publish.ps1 into .env (replace existing keys, append new ones, keep chmod 600).
if [[ -f "$ENV_UPDATE" ]]; then
  SUDO=""; [[ -w "$ENV_FILE" && -w "$SCRIPT_DIR" ]] || SUDO="sudo"
  $SUDO cp -p "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
  tmp="$(mktemp)"
  awk -F= 'NR==FNR { sub(/\r$/, ""); if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*=/) upd[$1]=1; next }
           { sub(/\r$/, "") } NF && !($1 in upd)' "$ENV_UPDATE" <($SUDO cat "$ENV_FILE") > "$tmp"
  tr -d '\r' < "$ENV_UPDATE" >> "$tmp"
  $SUDO tee "$ENV_FILE" < "$tmp" > /dev/null
  rm -f "$tmp"
  # Backups hold secrets too; keep the 3 newest, all 600.
  ls -1t "$ENV_FILE".bak-* 2>/dev/null | tail -n +4 | xargs -r $SUDO rm -f
  $SUDO chmod 600 "$ENV_FILE" "$ENV_FILE".bak-*
  $SUDO rm -f "$ENV_UPDATE" || echo "[WARN] Could not delete $ENV_UPDATE; remove it manually (it holds secrets)" >&2
  echo "[INFO] .env keys: $($SUDO cut -d= -f1 "$ENV_FILE" | paste -sd, -)"
fi

latest_tar="$(ls -1t "$TAR_DIR"/chip-jar-*.tar 2>/dev/null | head -n1 || true)"
if [[ -z "$latest_tar" ]]; then
  echo "No chip-jar-*.tar files found in $TAR_DIR" >&2
  exit 1
fi

echo "[INFO] Loading $latest_tar"
load_output="$(docker load -i "$latest_tar")"
echo "$load_output"
if [[ "$load_output" =~ Loaded\ image:\ ([^[:space:]]+) ]]; then
  new_image="${BASH_REMATCH[1]}"
  docker tag "$new_image" chip-jar:latest
else
  echo "[ERROR] Could not find the loaded image name" >&2
  exit 1
fi

old_tars="$(ls -1t "$TAR_DIR"/chip-jar-*.tar | tail -n +2 || true)"
if [[ -n "$old_tars" ]]; then
  echo "[INFO] Removing old tars:"; echo "$old_tars"
  echo "$old_tars" | xargs rm -f
fi

(cd "$SCRIPT_DIR/.." && docker compose up -d --force-recreate chip-jar)

echo "[INFO] Waiting for health check..."
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' chip-jar 2>/dev/null || echo missing)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
docker ps --filter name=chip-jar --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker logs --tail 15 chip-jar

# Only drop older images once the new one is healthy, so a bad deploy can still be rolled back.
if [[ "$status" == "healthy" ]]; then
  old_images="$(docker images chip-jar --format '{{.Repository}}:{{.Tag}}' \
    | grep -vxF -e chip-jar:latest -e "$new_image" || true)"
  if [[ -n "$old_images" ]]; then
    echo "[INFO] Removing old images:"; echo "$old_images"
    echo "$old_images" | xargs -r docker rmi || echo "[WARN] Some old images could not be removed" >&2
  fi
else
  echo "[WARN] chip-jar is '$status'; keeping older images for rollback" >&2
fi
