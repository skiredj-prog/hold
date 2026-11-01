"""Human reading of the journal (demo terminal). Language: HOLD_LANG."""

from __future__ import annotations

from .i18n import t
from .journal import Event

C = {"dim": "\033[2m", "amber": "\033[33m", "green": "\033[32m", "red": "\033[31m", "plum": "\033[35m", "bold": "\033[1m", "off": "\033[0m"}


def describe(e: Event) -> tuple[str, str] | None:
    """(tone, sentence); None for events without narrative interest."""
    d = e.data
    k = e.type
    if k == "PROFILE_ATTACHED":
        return "plum", t("n_profile", profile=d["profile"], revision=d["revision"], fp=d["fingerprint"][:12])
    if k == "ACTION_REFUSED":
        return "red", t("n_refused", rule=d["rule"], reason=d["reason"])
    if k == "EFFECT_RESERVED":
        return "dim", t("n_reserved", action=d["action"], op=d["operation_id"])
    if k == "EFFECT_OUTCOME":
        if d["status"] == "CONFIRMED":
            return "green", t("n_pass", receipt=d.get("receipt"))
        if d["status"] == "FAILED_PROVEN":
            return "red", t("n_fail", reason=d.get("reason"))
        return "amber", t("n_unknown", reason=d.get("reason"))
    if k == "HOLD_ENTERED":
        return "amber", t("n_hold", scope=", ".join(d["scope"]))
    if k == "DECISION":
        pos, v = d["position"], d["verdict"]
        if v["applied"] is None:
            return "amber", t("n_stale", round=d["round"])
        p = next((x for x in pos["paths"] if x["name"] == v["applied"]), None)
        sig = f" σ={p['sigma']:.2f} {'≥' if p['sigma'] >= pos['tau_star'] else '<'} τ*={pos['tau_star']:.2f}" if p and v["applied"] != "PRESERVE" else ""
        if p and p.get("directed"):
            sig += t("n_directed")
        who = t("n_overruled", proposed=v["proposed"]) if v["overruled"] else ""
        return "plum", t("n_decision", round=d["round"], path=v["applied"], sigma=sig, who=who)
    if k == "PATH_EXECUTED":
        return "dim", t("n_path", path=d["path"], result=d["result"], corr=t("n_correlated") if d.get("correlated") else "")
    if k == "RECONCILED":
        return "green", t("n_reconciled", receipt=d["receipt"], op=d["operation_id"])
    if k == "RESUMED":
        return "green", t("n_resumed")
    if k == "PRESERVED":
        return "amber", t("n_preserved", reason=d["reason"])
    if k == "DECISION_REQUEST":
        return "plum", t("n_request", to=d["to"], options=", ".join(o["code"] for o in d["options"]))
    if k == "RELAY_SENT":
        return "plum", t("n_relay", mode=d["mode"], to=d["to"])
    if k == "RELAY_FAILED":
        return "red", t("n_relay_failed", error=d["error"])
    if k == "AUTHORITY_ANSWER":
        return "green", t("n_answer", channel=d["channel"], answer=d["answer"])
    if k == "AUTHORITY_GRANT":
        return "green", t("n_grant", kind=d["grant"]["kind"])
    if k == "RELAY_REJECTED":
        return "red", t("n_rejected", reason=d["reason"])
    if k == "REVOKED":
        return "red", t("n_revoked", by=d["by"], channel=d["channel"])
    if k == "MISSION_CLOSED":
        return "bold", t("n_closed", outcome=d["outcome"])
    return None


def line(e: Event, color: bool = True) -> str | None:
    r = describe(e)
    if not r:
        return None
    tone, text = r
    if not color:
        return f"  {e.seq:>3} · {text}"
    return f"  {C['dim']}{e.seq:>3}{C['off']} {C.get(tone, '')}{text}{C['off']}"
