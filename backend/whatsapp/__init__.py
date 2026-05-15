"""Bot WhatsApp Business — orchestration audio → transcription → cours → PDF → envoi.

Architecture :
  - ``signature``  : vérification HMAC SHA-256 du payload entrant (anti-spoofing).
  - ``client``     : client HTTP minimal pour la Meta Cloud API (envoyer texte/document, télécharger média).
  - ``processor``  : machine à états du bot (dispatch des messages entrants, exécution end-to-end).
  - ``config``     : lecture centrale des variables d'env Meta (verify token, app secret, phone id, access token).
"""

from __future__ import annotations
