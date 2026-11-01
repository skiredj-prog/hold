"""Lecture humaine du journal (terminal de démonstration, HOLD Studio)."""

from __future__ import annotations

from .journal import Event

C = {"dim": "\033[2m", "amber": "\033[33m", "green": "\033[32m", "red": "\033[31m", "plum": "\033[35m", "bold": "\033[1m", "off": "\033[0m"}


def describe(e: Event) -> tuple[str, str] | None:
    """(tonalité, phrase) ; None pour les événements sans intérêt narratif."""
    d = e.data
    t = e.type
    if t == "PROFILE_ATTACHED":
        return "plum", f"Profil « {d['profile']} » rév. {d['revision']} attaché · empreinte {d['fingerprint'][:12]}…"
    if t == "ACTION_REFUSED":
        return "red", f"Refusé ({d['rule']}) : {d['reason']}"
    if t == "EFFECT_RESERVED":
        return "dim", f"{d['action']} réservé avant envoi · opération {d['operation_id']}"
    if t == "EFFECT_OUTCOME":
        if d["status"] == "CONFIRMED":
            return "green", f"PASS · reçu {d.get('receipt')}"
        if d["status"] == "FAILED_PROVEN":
            return "red", f"FAIL prouvé · {d.get('reason')}"
        return "amber", f"Résultat inconnu · {d.get('reason')}"
    if t == "HOLD_ENTERED":
        return "amber", f"HOLD · suspendu : {', '.join(d['scope'])} · le reste continue"
    if t == "DECISION":
        pos, v = d["position"], d["verdict"]
        if v["applied"] is None:
            return "amber", f"Tour {d['round']} · position périmée pendant la délibération : relire (I5)"
        p = next((x for x in pos["paths"] if x["name"] == v["applied"]), None)
        sig = f" σ={p['sigma']:.2f} {'≥' if p['sigma'] >= pos['tau_star'] else '<'} τ*={pos['tau_star']:.2f}" if p and v["applied"] != "PRESERVE" else ""
        if p and p.get("directed"):
            sig += " · accordé par l'autorité"
        who = "" if not v["overruled"] else f" (proposé : {v['proposed']}, refusé → référence)"
        return "plum", f"Tour {d['round']} · discernement → {v['applied']}{sig}{who}"
    if t == "PATH_EXECUTED":
        return "dim", f"{d['path']} → {d['result']}{' (corrélé)' if d.get('correlated') else ''}"
    if t == "RECONCILED":
        return "green", f"Réconcilié · reçu {d['receipt']} corrélé à {d['operation_id']} · rien n'a été répété"
    if t == "RESUMED":
        return "green", "Reprise autonome de la mission"
    if t == "PRESERVED":
        return "amber", f"PRESERVE · {d['reason']}"
    if t == "DECISION_REQUEST":
        return "plum", f"Decision Request → {d['to']} : {', '.join(o['code'] for o in d['options'])}"
    if t == "RELAY_SENT":
        return "plum", f"WhatsApp ({d['mode']}) → {d['to']}"
    if t == "RELAY_FAILED":
        return "red", f"WhatsApp en échec : {d['error']}"
    if t == "AUTHORITY_ANSWER":
        return "green", f"Réponse de l'autorité ({d['channel']}) : {d['answer']}"
    if t == "AUTHORITY_GRANT":
        return "green", f"Accordé par l'autorité : {d['grant']['kind']}"
    if t == "RELAY_REJECTED":
        return "red", f"Réponse refusée : {d['reason']}"
    if t == "REVOKED":
        return "red", f"Mandat révoqué par {d['by']} ({d['channel']}) : plus aucune action"
    if t == "MISSION_CLOSED":
        return "bold", f"Mission close : {d['outcome']}"
    return None


def line(e: Event, color: bool = True) -> str | None:
    r = describe(e)
    if not r:
        return None
    tone, text = r
    if not color:
        return f"  {e.seq:>3} · {text}"
    return f"  {C['dim']}{e.seq:>3}{C['off']} {C.get(tone, '')}{text}{C['off']}"
