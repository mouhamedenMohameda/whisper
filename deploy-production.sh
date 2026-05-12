#!/usr/bin/env bash
# Déploiement vers le VPS : backup SQLite local (si présent) → rsync code → backup SQLite distant → deps → build → PM2.
#
# Usage (depuis n’importe où) :
#   bash /chemin/vers/LectureAI/lecturai/deploy-production.sh
#
# Surcharges optionnelles :
#   export DEPLOY_HOST=5.189.153.144
#   export DEPLOY_USER=root
#   export REMOTE_BASE=/var/www/ai-whisper
#   export SSH_KEY=$HOME/.ssh/id_ed25519_lecturai

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-5.189.153.144}"
DEPLOY_USER="${DEPLOY_USER:-root}"
REMOTE_BASE="${REMOTE_BASE:-/var/www/ai-whisper}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_lecturai}"

# Répertoire du paquet lecturai (ce fichier vit dans lecturai/).
LECTURAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SSH=(ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}")
RSYNC=(rsync -avz -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no")

LOCAL_DB="${LECTURAI_DIR}/backend/data/lecturai.db"
LOCAL_BACKUP_DIR="${LECTURAI_DIR}/backend/data/db-backups-local"

echo "=== 1) Backup SQLite local (Mac), si ${LOCAL_DB} existe ==="
if [[ -f "${LOCAL_DB}" ]]; then
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "ERREUR: sqlite3 absent sur le Mac (brew install sqlite). Backup local impossible."
    exit 1
  fi
  mkdir -p "${LOCAL_BACKUP_DIR}"
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  DEST_LOCAL="${LOCAL_BACKUP_DIR}/pre-deploy-${STAMP}.db"
  sqlite3 "${LOCAL_DB}" ".backup '${DEST_LOCAL}'"
  echo "    OK → ${DEST_LOCAL}"
else
  echo "    (aucun lecturai.db local — rien à sauvegarder)"
fi

echo "=== 2) rsync code (sans backend/data/) ==="
"${RSYNC[@]}" \
  --exclude node_modules \
  --exclude .git \
  --exclude .env \
  --exclude '.env.*' \
  --exclude '**/__pycache__' \
  --exclude '.venv' \
  --exclude 'backend/data/' \
  "${LECTURAI_DIR}/" "${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_BASE}/"

echo "=== 3) Backup SQLite sur le VPS + deps + build + PM2 ==="
"${SSH[@]}" bash -s -- "${REMOTE_BASE}" <<'REMOTE_EOF'
set -euo pipefail
REMOTE_BASE="$1"
cd "${REMOTE_BASE}/backend"
if [[ -f scripts/backup_sqlite.sh ]]; then
  bash scripts/backup_sqlite.sh
else
  echo "AVERTISSEMENT: scripts/backup_sqlite.sh introuvable — saute le backup distant."
fi
.venv/bin/pip install -q -r requirements.txt
cd "${REMOTE_BASE}/frontend"
npm ci
npm run build
pm2 restart ai-whisper --update-env
echo "=== Déploiement distant terminé. ==="
REMOTE_EOF

echo "=== Terminé. ==="
