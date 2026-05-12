#!/usr/bin/env bash
# Sauvegarde cohérente de la base SQLite LecturAI (à lancer à chaque déploiement ou via cron).
#
# Usage (sur le serveur, depuis n’importe quel répertoire) :
#   /var/www/ai-whisper/backend/scripts/backup_sqlite.sh
#
# Variables optionnelles :
#   LECTURAI_SQLITE_PATH   chemin absolu vers lecturai.db (défaut : <backend>/data/lecturai.db)
#   LECTURAI_BACKUP_DIR    dossier des copies (défaut : <backend>/data/db-backups)
#   LECTURAI_BACKUP_KEEP   nombre de copies à conserver (défaut : 30)
#
# Automatique :
#   - Cron quotidien : une fois sur le VPS, lancer install_backup_cron.sh (voir scripts/).
#   - À chaque déploiement : utiliser pm2_reload_with_backup.sh à la place de « pm2 reload ».
#
# Manuel :
#   bash /var/www/ai-whisper/backend/scripts/backup_sqlite.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB="${LECTURAI_SQLITE_PATH:-${BACKEND_ROOT}/data/lecturai.db}"
BACKUP_DIR="${LECTURAI_BACKUP_DIR:-${BACKEND_ROOT}/data/db-backups}"
KEEP="${LECTURAI_BACKUP_KEEP:-30}"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB}" ]]; then
  echo "backup_sqlite: aucun fichier ${DB} — rien à sauvegarder."
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR}/lecturai-${STAMP}.db"

# Copie cohérente (recommandé plutôt qu’un simple cp si l’API écrit en base).
sqlite3 "${DB}" ".backup '${DEST}'"

echo "backup_sqlite: OK → ${DEST}"

# Rotation : garde les KEEP plus récentes, supprime le reste.
if [[ "${KEEP}" =~ ^[0-9]+$ ]] && [[ "${KEEP}" -gt 0 ]]; then
  mapfile -t OLD < <(ls -1t "${BACKUP_DIR}"/lecturai-*.db 2>/dev/null | tail -n "+$((KEEP + 1))" || true)
  for f in "${OLD[@]:-}"; do
    [[ -n "${f}" ]] || continue
    rm -f "${f}"
    echo "backup_sqlite: supprimé ancienne copie ${f}"
  done
fi
