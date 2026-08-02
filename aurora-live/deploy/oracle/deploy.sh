#!/usr/bin/env bash
# Run from the repo root on the OCI VM:
#   cd /opt/aurora-live && bash deploy/oracle/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
COMPOSE="deploy/oracle/docker-compose.oci.yml"
ENVF=".env.oci"

if [[ ! -f "$ENVF" ]]; then
  cp deploy/oracle/.env.oci.example "$ENVF"
  # generate secrets if openssl present
  if command -v openssl >/dev/null 2>&1; then
    sed -i.bak \
      -e "s/CHANGE_ME_long_random_db_password/$(openssl rand -hex 24)/" \
      -e "s/CHANGE_ME_long_random_bootstrap/$(openssl rand -hex 24)/" \
      -e "s/CHANGE_ME_long_random_webhook/$(openssl rand -hex 24)/" \
      "$ENVF" || true
    rm -f "${ENVF}.bak"
  fi
  echo "Created $ENVF — review secrets if needed."
fi

# Detect public IP (best-effort)
PUB_IP="$(curl -4 -fsS --max-time 5 ifconfig.me 2>/dev/null || curl -4 -fsS --max-time 5 icanhazip.com 2>/dev/null || true)"
if [[ -n "${PUB_IP}" ]]; then
  echo "Detected public IP: $PUB_IP"
  # If still wildcard, leave as-is (open beta). Optionally pin hosts:
  # grep -q 'AURORA_ALLOWED_HOSTS=\*' "$ENVF" && sed -i "s|AURORA_ALLOWED_HOSTS=\*|AURORA_ALLOWED_HOSTS=${PUB_IP},localhost,127.0.0.1|" "$ENVF"
fi

echo "Building and starting AURORA (this can take several minutes on ARM free tier)..."
docker compose -f "$COMPOSE" --env-file "$ENVF" up -d --build

echo "Waiting for live..."
for i in $(seq 1 60); do
  if curl -fsS --max-time 3 http://127.0.0.1:8090/api/platform/live >/dev/null 2>&1; then
    echo "API is up."
    break
  fi
  sleep 3
done

curl -fsS http://127.0.0.1:8090/api/platform/live || true
echo
curl -fsS http://127.0.0.1:8090/api/platform/me || true
echo

echo "========================================"
echo " LOCAL on VM:  http://127.0.0.1:8090/platform"
if [[ -n "${PUB_IP}" ]]; then
  echo " PUBLIC:       http://${PUB_IP}:8090/platform"
  echo " (open TCP 8090 in OCI Security List + host firewall)"
fi
echo " Open beta: no password if AURORA_OPEN_ACCESS=1"
echo " Logs: docker compose -f $COMPOSE --env-file $ENVF logs -f"
echo "========================================"
