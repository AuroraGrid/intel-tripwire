#!/usr/bin/env bash
# Run ONCE on a fresh Oracle Linux / Ubuntu Always Free VM as a sudo user.
# Usage:
#   curl -fsSL ... | bash   # or
#   bash setup-vm.sh
set -euo pipefail

echo "=== AURORA OCI free-tier VM setup ==="

if [[ $EUID -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

# --- packages ---
if command -v dnf >/dev/null 2>&1; then
  $SUDO dnf -y update || true
  $SUDO dnf -y install curl git ca-certificates
  # Docker CE on Oracle Linux / RHEL-like
  if ! command -v docker >/dev/null 2>&1; then
    $SUDO dnf -y install dnf-plugins-core || true
    $SUDO dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo || true
    $SUDO dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin || {
      echo "Docker CE repo failed — trying podman-docker / alternative"
      $SUDO dnf -y install docker docker-compose || $SUDO yum -y install docker
    }
  fi
elif command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl git
  if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | $SUDO sh
  fi
else
  echo "Unsupported package manager. Install Docker manually."
  exit 1
fi

$SUDO systemctl enable --now docker
if id -nG "$USER" 2>/dev/null | grep -qw docker; then
  :
else
  $SUDO usermod -aG docker "$USER" || true
  echo "NOTE: log out/in so docker group applies (or use sudo docker)."
fi

# firewalld / ufw
if command -v firewall-cmd >/dev/null 2>&1; then
  $SUDO firewall-cmd --permanent --add-service=http || true
  $SUDO firewall-cmd --permanent --add-service=https || true
  $SUDO firewall-cmd --permanent --add-port=8090/tcp || true
  $SUDO firewall-cmd --reload || true
elif command -v ufw >/dev/null 2>&1; then
  $SUDO ufw allow 22/tcp || true
  $SUDO ufw allow 80/tcp || true
  $SUDO ufw allow 443/tcp || true
  $SUDO ufw allow 8090/tcp || true
fi

echo "Docker: $(docker --version 2>/dev/null || echo missing)"
echo "Compose: $(docker compose version 2>/dev/null || echo missing)"
echo "=== setup-vm.sh complete ==="
echo "Next: copy the aurora-live repo to /opt/aurora-live and run deploy.sh"
