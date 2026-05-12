#!/usr/bin/env bash
# Enregistre une tâche cron (root) : sauvegarde SQLite tous les jours à 03:00.
# À lancer UNE fois sur le VPS après déploiement des scripts.
#
# Usage :
#   sudo bash /var/www/ai-whisper/backend/scripts/install_backup_cron.sh
#   sudo bash .../install_backup_cron.sh /autre/chemin/backend
#
# Désinstaller la ligne LecturAI :
#   sudo crontab -l | grep -vF '/scripts/backup_sqlite.sh' | sudo crontab -

set -euo pipefail

BACKEND_ROOT="${1:-/var/www/ai-whisper/backend}"
SCRIPT="${BACKEND_ROOT}/scripts/backup_sqlite.sh"
LOG="${BACKEND_ROOT}/data/db-backups/cron.log"

if [[ ! -f "${SCRIPT}" ]]; then
  echo "install_backup_cron: script introuvable : ${SCRIPT}"
  exit 1
fi

mkdir -p "${BACKEND_ROOT}/data/db-backups"
chmod +x "${SCRIPT}"

CRON_LINE="0 3 * * * bash ${SCRIPT} >>${LOG} 2>&1"

TMP="$(mktemp)"
( crontab -l 2>/dev/null | grep -vF "${SCRIPT}" || true
  echo "${CRON_LINE}"
) > "${TMP}"
crontab "${TMP}"
rm -f "${TMP}"

echo "install_backup_cron: crontab mis à jour — sauvegarde quotidienne 03:00 → ${SCRIPT}"
echo "install_backup_cron: logs → ${LOG}"
crontab -l | grep -F "${SCRIPT}" || true
