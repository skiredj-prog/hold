"""Langue de présentation : HOLD_LANG=en (défaut) ou fr.

Le journal est toujours écrit en anglais (langue d'enregistrement stable, rejouable à l'identique
par la bibliothèque et par HOLD Studio). La langue ne change que ce qui est montré : narration,
messages renvoyés à l'agent, sorties de démonstration. Les messages WhatsApp suivent la langue
de l'autorité déclarée dans le profil (`authority.locale`).
"""

from __future__ import annotations

import os

MESSAGES: dict[str, dict[str, str]] = {
    # -- messages renvoyés à l'agent ------------------------------------------------------
    "closed": {"en": "Mission {status}: no action.", "fr": "Mission {status} : aucune action."},
    "no_impl": {"en": "No implementation registered for “{action}”.", "fr": "Aucune implémentation enregistrée pour « {action} »."},
    "envelope_held": {"en": "{reason}. Decision Request sent to {authority}; do not try to work around it.", "fr": "{reason}. Decision Request envoyée à {authority} ; n'essayez pas de contourner."},
    "done": {"en": "{action}: done.", "fr": "{action} : terminé."},
    "failed": {"en": "{action} failed: {error}", "fr": "{action} a échoué : {error}"},
    "already": {"en": "Already done and confirmed (receipt {receipt}). Nothing was repeated.", "fr": "Déjà fait et confirmé (reçu {receipt}). Rien n'a été répété."},
    "confirmed": {"en": "{action} confirmed (receipt {receipt}).", "fr": "{action} confirmé (reçu {receipt})."},
    "rejected": {"en": "{action} refused before creation: {reason}. One new attempt is admissible.", "fr": "{action} refusé avant création : {reason}. Un nouvel essai est admissible."},
    "held": {
        "en": "HOLD: the outcome of {action} ({op}) is unknown. Do not retry and do not create anything equivalent; HOLD is reconciling it. Dependent actions are suspended; independent work may continue.",
        "fr": "HOLD : le résultat de {action} ({op}) est inconnu. Ne réessayez pas et ne créez rien d'équivalent ; HOLD conduit la réconciliation. Les actions dépendantes sont suspendues, le travail indépendant peut continuer.",
    },
    "held_request": {"en": " A Decision Request is open with {authority}.", "fr": " Decision Request ouverte auprès de {authority}."},
    "grant_used": {"en": "{action} refused: the authority's one-time approval was already used.", "fr": "{action} refusé : l'autorisation unique de l'autorité a déjà servi."},
    "reconciled": {"en": "{action} confirmed after reconciliation (receipt {receipt}). Nothing was repeated; the mission resumes.", "fr": "{action} confirmé après réconciliation (reçu {receipt}). Rien n'a été répété ; la mission reprend."},
    # -- narration ------------------------------------------------------------------------
    "n_profile": {"en": "Profile “{profile}” rev. {revision} attached · fingerprint {fp}…", "fr": "Profil « {profile} » rév. {revision} attaché · empreinte {fp}…"},
    "n_refused": {"en": "Refused ({rule}): {reason}", "fr": "Refusé ({rule}) : {reason}"},
    "n_reserved": {"en": "{action} reserved before sending · operation {op}", "fr": "{action} réservé avant envoi · opération {op}"},
    "n_pass": {"en": "PASS · receipt {receipt}", "fr": "PASS · reçu {receipt}"},
    "n_fail": {"en": "FAIL, proven · {reason}", "fr": "FAIL prouvé · {reason}"},
    "n_unknown": {"en": "Outcome unknown · {reason}", "fr": "Résultat inconnu · {reason}"},
    "n_hold": {"en": "HOLD · suspended: {scope} · everything else continues", "fr": "HOLD · suspendu : {scope} · le reste continue"},
    "n_stale": {"en": "Round {round} · position changed during deliberation: re-read (I5)", "fr": "Tour {round} · position périmée pendant la délibération : relire (I5)"},
    "n_decision": {"en": "Round {round} · discernment → {path}{sigma}{who}", "fr": "Tour {round} · discernement → {path}{sigma}{who}"},
    "n_directed": {"en": " · granted by the authority", "fr": " · accordé par l'autorité"},
    "n_overruled": {"en": " (proposed {proposed}, refused → reference)", "fr": " (proposé : {proposed}, refusé → référence)"},
    "n_path": {"en": "{path} → {result}{corr}", "fr": "{path} → {result}{corr}"},
    "n_correlated": {"en": " (correlated)", "fr": " (corrélé)"},
    "n_reconciled": {"en": "Reconciled · receipt {receipt} correlated with {op} · nothing was repeated", "fr": "Réconcilié · reçu {receipt} corrélé à {op} · rien n'a été répété"},
    "n_resumed": {"en": "The mission resumes on its own", "fr": "Reprise autonome de la mission"},
    "n_preserved": {"en": "PRESERVE · {reason}", "fr": "PRESERVE · {reason}"},
    "n_request": {"en": "Decision Request → {to}: {options}", "fr": "Decision Request → {to} : {options}"},
    "n_relay": {"en": "WhatsApp ({mode}) → {to}", "fr": "WhatsApp ({mode}) → {to}"},
    "n_relay_failed": {"en": "WhatsApp failed: {error}", "fr": "WhatsApp en échec : {error}"},
    "n_answer": {"en": "Authority's answer ({channel}): {answer}", "fr": "Réponse de l'autorité ({channel}) : {answer}"},
    "n_grant": {"en": "Granted by the authority: {kind}", "fr": "Accordé par l'autorité : {kind}"},
    "n_rejected": {"en": "Answer refused: {reason}", "fr": "Réponse refusée : {reason}"},
    "n_revoked": {"en": "Mandate revoked by {by} ({channel}): no further action", "fr": "Mandat révoqué par {by} ({channel}) : plus aucune action"},
    "n_closed": {"en": "Mission closed: {outcome}", "fr": "Mission close : {outcome}"},
    # -- démonstration ---------------------------------------------------------------------
    "d_baseline": {"en": "Baseline without HOLD: the agent calls the provider directly.", "fr": "Témoin sans HOLD : l'agent appelle le fournisseur directement."},
    "d_attempt_ok": {"en": "  attempt {n} → {status} {receipt} · the agent announces success", "fr": "  essai {n} → {status} {receipt} · l'agent annonce le succès"},
    "d_attempt_timeout": {"en": "  attempt {n} → timeout · the agent concludes it failed and tries again", "fr": "  essai {n} → délai dépassé · l'agent conclut à un échec et recommence"},
    "d_baseline_count": {"en": "  Provider's ledger: {n} booking(s) for a single stay.", "fr": "  Registre du fournisseur : {n} réservation(s) pour un seul séjour."},
    "d_server": {"en": "HOLD server: http://127.0.0.1:{port}  (Studio, webhook /api/whatsapp/webhook)", "fr": "Serveur HOLD : http://127.0.0.1:{port}  (Studio, webhook /api/whatsapp/webhook)"},
    "d_mission": {"en": "Mission {id} · scenario “{scenario}” · agent {agent} · discerner {discerner} · relay {relay}", "fr": "Mission {id} · scénario « {scenario} » · agent {agent} · discerneur {discerner} · relais {relay}"},
    "d_agent": {"en": "agent ▸ {name} → {status}{msg}", "fr": "agent ▸ {name} → {status}{msg}"},
    "d_waiting": {"en": "Waiting for the authority ({authority}) · question {q} · options {options}", "fr": "En attente de l'autorité ({authority}) · question {q} · options {options}"},
    "d_simulated_tap": {"en": "(simulation) the authority taps on WhatsApp: {code}", "fr": "(simulation) tap WhatsApp de l'autorité : {code}"},
    "d_report": {"en": "agent ▸ report: {report}", "fr": "agent ▸ rapport : {report}"},
    "d_summary": {"en": "Provider: {bookings} booking(s) · journal {chain} ({events} events) · status {status}", "fr": "Fournisseur : {bookings} réservation(s) · journal {chain} ({events} événements) · statut {status}"},
    "d_valid": {"en": "valid", "fr": "valide"},
    "d_invalid": {"en": "INVALID", "fr": "INVALIDE"},
    "d_done_report": {"en": "Hotel confirmed (receipt {receipt}), itinerary sent.", "fr": "Hôtel confirmé (reçu {receipt}), itinéraire envoyé."},
    "d_pending_report": {"en": "Booking not confirmed for now ({status}): nothing was created twice.", "fr": "Réservation non confirmée pour l'instant ({status}) : rien n'a été recréé."},
    "d_still": {"en": "Still unconfirmed.", "fr": "Toujours non confirmé."},
    "d_notice": {"en": "HOLD · mission completed. {report}", "fr": "HOLD · mission terminée. {report}"},
    "d_title": {"en": "Trip to Lyon — {label}", "fr": "Déplacement à Lyon — {label}"},
}


def lang() -> str:
    return "fr" if os.environ.get("HOLD_LANG", "en").lower().startswith("fr") else "en"


def t(key: str, **kw: object) -> str:
    return MESSAGES[key][lang()].format(**kw)
