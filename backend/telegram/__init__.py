"""Bot Telegram LecturAI — miroir du bot WhatsApp.

Sous-modules :
  - ``config``        : lecture des variables d'env TELEGRAM_*.
  - ``client``        : wrapper HTTP minimal autour de l'API Bot Telegram.
  - ``parser``        : Update Telegram → ``InboundTgMessage`` normalisé.
  - ``secret_token``  : vérification du header ``X-Telegram-Bot-Api-Secret-Token``.
  - ``processor``     : orchestration (commandes + pipeline audio → PDF).
"""
