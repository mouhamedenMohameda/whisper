# TODO list — LecturAI

## 🔴 Bloquant prod

- [ ] **Régénérer le token WhatsApp Meta** (expire toutes les 24h en sandbox).
  - Symptôme : logs `Meta API 401: Authentication Error` dans `pm2 logs ai-whisper`.
  - Fix court : régénérer un token sandbox depuis https://developers.facebook.com/apps/997367982766523/whatsapp-business/wa-dev-console/ → remplacer `WHATSAPP_ACCESS_TOKEN` dans `/var/www/ai-whisper/backend/.env` → `pm2 restart ai-whisper`.
  - Fix durable : créer un **System User Token** (never expires) dans Business Settings → Users → System Users avec permissions `whatsapp_business_messaging` + `whatsapp_business_management`.

## 🟠 Sécurité — rate-limiting auth perdu

Les décorateurs `@limiter.limit` (slowapi 0.1.9) ont été retirés des routes `/api/auth/register`, `/api/auth/login`, `/api/auth/reset-password` car ils crashaient FastAPI 0.115 + Pydantic 2.13 (`ForwardRef('RegisterBody') is not fully defined`).

Conséquence : brute-force sur `/auth/login` n'est plus freiné côté app (seul nginx peut limiter).

Options pour rétablir :

- [ ] **Option A : upgrade slowapi** — `pip install --upgrade slowapi` puis remettre `@limiter.limit` sur les 3 routes. Tester un signup pour confirmer que le bug d'introspection est corrigé dans la nouvelle version.
- [ ] **Option B : rate-limit via dépendance FastAPI** — petit middleware perso (compteur in-memory ou Redis) injecté via `Depends`, qui ne wrappe pas la fonction et n'a donc pas le bug.
- [ ] **Option C : `fastapi-limiter`** (basée Redis) — propre mais ajoute une dépendance Redis.

## 🟠 Audit autres routes affectées par le bug slowapi

Toutes les routes qui combinent `@limiter.limit` + body Pydantic ont le même bug latent et planteront au premier appel. À identifier :

- [ ] Lancer `grep -l "@limiter.limit" lecturai/backend/routes/*.py` (ou sur le VPS).
- [ ] Pour chaque route trouvée : tester avec un curl POST → si crash `ForwardRef` ou 422 `loc:["query","body"]`, retirer `@limiter.limit` (idem auth) et noter ici pour le remettre après le fix global ci-dessus.

## 🟢 Améliorations bot WhatsApp (déjà implémentées localement, restent à valider en prod)

Code rapatrié sur le VPS : ✅ `whatsapp/*` + `models.py` + `schema_migrate.py`.

À tester une fois le token WhatsApp régénéré :

- [ ] Statut intermédiaire pendant transcription/génération
- [ ] `/matiere <nom>` + auto-détection par mots-clés
- [ ] `/refaire` — re-génère la dernière fiche sans re-payer la transcription
- [ ] `/langue fr|ar` — persistance via `User.whatsapp_language`
- [ ] Lien web `/c/<token>` envoyé après le PDF (requiert `PUBLIC_APP_BASE_URL=https://ai-whisper.radar-mr.com` dans `.env`)
- [ ] Quiz interactif (boutons / liste Meta) après le PDF + `/quiz`
- [ ] Onboarding amélioré (welcome enrichi pour numéros inconnus)

## 🟢 Setup WhatsApp Business Verification (en attente)

- [ ] Email d'approbation Meta sur `mohameda.mouhameden@gmail.com` (Business Verification soumise le 2026-05-15 via Domain Verification)
- [ ] Submit App Review (3 permissions) une fois la Verification ✅
- [ ] Passer l'app en mode Live (toggle Development → Live)
- [ ] Ajouter `+222 32164356` (ou `+222 47060419`) au WABA RIM MIND AI - SUARL
- [ ] Mettre à jour `WHATSAPP_PHONE_NUMBER_ID` dans `.env` avec le nouveau Phone Number ID

## 🟡 Côté déploiement

- [ ] Documenter / scripter le déploiement VPS (actuellement édits sed in-place sur le VPS → fragile). Au minimum un `rsync` ou un `git pull` depuis un repo de référence, pour qu'on n'ait plus à patcher fichier par fichier.

## 🟡 Diverses pistes d'amélioration bot (proposées le 2026-05-15, non implémentées)

Voir aussi la conversation initiale pour le détail.

- [ ] Devis avant traitement des audios > 10 min (anti-surprise wallet)
- [ ] Notification de seuil bas (wallet < 50 MRU)
- [ ] Système de parrainage `/parrainer` (code → bonus pour les deux)
- [ ] Rate-limit Redis (au lieu du dict in-memory `_last_audio_at`)
- [ ] Remplacer le polling DB (`_wait_for_transcription`) par `asyncio.Event` ou pg LISTEN/NOTIFY
- [ ] Tests d'intégration du flow webhook → PDF (audio fixture + mock Meta)
- [ ] Compression audio entrant (opus 16kHz mono) avant transcription
- [ ] Limite de durée audio (refus propre > 30 min au lieu d'attendre le timeout 15 min)
- [ ] Métriques par étape (download / transcription / génération / upload) → Prometheus / Grafana
- [ ] Dashboard usage (users actifs/jour, taux d'échec, coût moyen)
- [ ] Sentry pour capter les `logger.exception` avec contexte
