# LecturAI

Transforme des enregistrements de cours en parcours structuré (transcription, leçon interactive, quiz, fiches, exports).

## Setup in three commands

From the repo root:

```bash
cd lecturai/backend && python -m pip install -r requirements.txt && cp ../.env.example .env
```

```bash
cd ../frontend && npm install
```

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Run the third command inside `lecturai/backend` after `.env` is filled. Launch the UI with `npm run dev` inside `frontend/` (separate terminal) → http://localhost:5173 (Vite proxies `/api` to `8000` automatically).

### Authentification utilisateur

Les comptes sont stockés localement (**SQLite**, fichier `backend/data/lecturai.db` par défaut). À **l’inscription** : **e-mail**, **mot de passe**, **NNI** et **numéro WhatsApp** (format international, ex. `+222…`) sont enregistrés. À la **connexion** : e-mail + mot de passe. Pour un **nouveau mot de passe sans être connecté** : l’utilisateur saisit à nouveau **le même e-mail, le même NNI et le même numéro WhatsApp** que lors de l’inscription, plus le **nouveau mot de passe** — **aucun message WhatsApp automatique**, pas de code OTP.

- **Variables obligatoires** avec `AUTH_REQUIRED=true` : `JWT_SECRET` (au moins 16 caractères).
- **`AUTH_REQUIRED=false`** désactive la protection des routes métier (`/api/transcribe`, `/api/generate`, exports) ; inscription / connexion API renvoient 403 dans ce mode.

Endpoints utiles : `GET /api/auth/config`, `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/reset-password`, `GET /api/auth/me` (Bearer JWT).

### Crédits (si `AUTH_REQUIRED=true`)

Chaque compte dispose d’un **solde entier** et d’une **date de validité**. La **transcription**, la **génération du cours** et chaque **export PDF/DOCX** réussis **consomment** des crédits (montants configurables : `CREDITS_DEBIT_TRANSCRIBE`, `CREDITS_DEBIT_GENERATE`, `CREDITS_DEBIT_EXPORT`). Si le solde est **≤ 0** ou si la validité est **dépassée**, les routes métier renvoient **403** jusqu’à recharge.

**Utilisateur** : dans l’app, ouvre **« Crédits »** ; envoie une **capture du virement** (`POST /api/credits/topup-requests`, image ≤ 6 Mo). L’historique affiche les statuts : attente / validée / refusée.

**Administrateur (un seul compte)** : dans `.env`, définis `ADMIN_EMAIL` sur l’**e-mail exact** du compte qui doit valider les recharges (même chaîne qu’à l’inscription, insensible à la casse). Au **démarrage** du serveur, tous les `is_admin` sont recalculés : un seul utilisateur correspondant à cet e-mail obtient les droits admin ; les autres ont `is_admin=false`. Les routes ci-dessous exigent un **JWT Bearer** (`Authorization: Bearer …`) de ce compte ; `AUTH_REQUIRED=true` est obligatoire pour ces endpoints.

| Méthode | Route | Rôle |
|--------|-------|-----|
| `GET` | `/api/admin/credit-topups?status=pending` | liste des demandes (`status=pending` \| `all` \| `approved` \| `rejected`) |
| `GET` | `/api/admin/credit-topups/{id}/proof` | télécharger / afficher la preuve (JWT admin) |
| `POST` | `/api/admin/credit-topups/{id}/approve` | corps JSON : `credit_amount`, optionnel `extend_validity_days` (sinon défaut env), optionnel `admin_note` |
| `POST` | `/api/admin/credit-topups/{id}/reject` | corps JSON : optionnel `admin_note` |

Exemple (obtiens d’abord un token via `POST /api/auth/login`) :

`curl -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d '{"credit_amount":100,"extend_validity_days":180}' https://TON_BACKEND/api/admin/credit-topups/1/approve`

Dans l’app web connectée en admin, le bouton **« Valid. recharges »** ouvre la même validation (prévisualisation des preuves avec le token).

À l’**inscription**, bonus initial : `CREDITS_REGISTRATION_BONUS`, validité : `CREDITS_REGISTRATION_VALIDITY_DAYS`. Les anciennes bases SQLite reçoivent les colonnes `credit_balance` / `credits_expire_at` au prochain démarrage (`schema_migrate`).

## Deploy

- **Frontend (Vercel)**  
  - Root directory: `lecturai/frontend`  
  - Build: `npm run build`  
  - Output: `dist`  
  - Environment: `VITE_API_URL=https://your-backend-host`  

- **Backend (Railway / Render)**  
  - Root directory: `lecturai/backend`  
  - Start: `python -m uvicorn main:app --host 0.0.0.0 --port $PORT` (Render exposes `PORT`; Railway injects similarly)  
  - Environment: `OPENAI_API_KEY`, `GROQ_API_KEY` (génération de cours `/api/generate` + analyse transcript), `JWT_SECRET`, `ADMIN_EMAIL` (compte unique pour `/api/admin/*`), `AUTH_REQUIRED=true`, volume persistant pour `backend/data` (captures + SQLite), ou `DATABASE_URL` PostgreSQL, `ALLOWED_ORIGINS=https://your-vercel-domain.app`  

### Backend (VPS + SQLite) — comptes qui « disparaissent » à chaque déploiement

L’application **ne supprime pas** la table `users` au redémarrage. Si tout le monde est déconnecté après un déploiement, c’est en général que **le fichier `lecturai.db` (ou tout `backend/data/`) a été remplacé** par une copie vide ou neuve.

Causes typiques : **rsync/scp** avec `--delete` qui recopie un `backend/` sans ta base de prod ; **suppression** du dossier `/var/www/...` puis re-clone ; **Docker** sans volume sur `data/` ; **CI** qui publie un artefact « propre » par-dessus la prod.

**Correctifs possibles** (choisir au moins une ligne de conduite) :

1. **Ne pas écraser les données au déploiement** — synchroniser seulement le code (ex. `backend/*.py`, `routes/`, etc.) ou **exclure** explicitement `data/` / `lecturai.db` des rsync ; éviter `git clean -fdx` sur le serveur dans le dossier d’install.
2. **Sortir la base du répertoire déployé** — sur le serveur, dans `.env` :  
   `DATABASE_URL=sqlite:////var/lib/lecturai/lecturai.db`  
   (créer le répertoire, droits d’écriture pour l’utilisateur PM2), puis une seule fois copier l’ancienne base si besoin. Les prochains déploiements du code ne toucheront plus ce fichier.
3. **Sauvegardes automatiques** — `backend/scripts/backup_sqlite.sh` + `install_backup_cron.sh` (voir en-têtes des scripts).
4. **Déploiement depuis un Mac** (rsync, backup SQLite local + distant, build, PM2) — script `lecturai/deploy-production.sh` (variables `DEPLOY_HOST`, `SSH_KEY`, etc. en en-tête).

## Stack

FastAPI • pipeline transcription + génération de cours (APIs externes configurables) • React • Tailwind • react-markdown • ReportLab PDF • python-docx
