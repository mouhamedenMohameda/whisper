#!/usr/bin/env bash
# Sauvegarde la base puis recharge l’app PM2 (à utiliser à la place de « pm2 reload » lors d’un déploiement).
#
# Usage :
#   bash /var/www/ai-whisper/backend/scripts/pm2_reload_with_backup.sh ai-whisper

set -euo pipefail

APP_NAME="${1:-ai-whisper}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/backup_sqlite.sh"
pm2 reload "${APP_NAME}" --update-env

echo "pm2_reload_with_backup: OK — ${APP_NAME} rechargé."
