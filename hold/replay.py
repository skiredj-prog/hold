"""Rejeu à partir du seul journal, sans rappeler ni le modèle ni le fournisseur.

- state_replay  : reconstruit la projection (effets, compteurs, demandes, statut) et la compare au magasin.
- decision_replay : recalcule chaque verdict du plancher à partir des entrées enregistrées.
- policy_replay   : rejuge les positions enregistrées sous un autre τ* ou une autre échelle (contrefactuel).
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .floor import admit_path
from .hesitation import PathState, reference_path
from .journal import Event
from .profile import Profile


def fold(events: list[Event], profile: Profile) -> dict[str, Any]:
    status = "OPEN"
    revoked = False
    effects: dict[str, dict[str, Any]] = {}
    usage: dict[str, int] = defaultdict(int)
    requests: dict[str, str] = {}
    grants_consumed: set[str] = set()

    def budget(action: str) -> bool:
        p = profile.power(action)
        return bool(p and p.budget is not None)

    for e in events:
        d = e.data
        t = e.type
        if t == "ACTION_ADMITTED":
            if d.get("kind") != "effect" and budget(d["action"]):
                usage[f"power:{d['action']}"] += 1
            if d.get("grant"):
                grants_consumed.add(d["grant"])
        elif t == "EFFECT_RESERVED":
            prev = effects.get(d["effect_key"])
            effects[d["effect_key"]] = {"action": d["action"], "operation_id": d["operation_id"], "status": "DISPATCHING", "receipt": None, "not_found": 0, "attempts": (prev["attempts"] + 1) if prev else 1}
            if budget(d["action"]):
                usage[f"power:{d['action']}"] += 1
        elif t == "EFFECT_OUTCOME":
            eff = effects[d["effect_key"]]
            eff["status"] = d["status"]
            if d["status"] == "CONFIRMED":
                eff["receipt"] = d.get("receipt")
            if d["status"] == "FAILED_PROVEN" and budget(eff["action"]):
                usage[f"power:{eff['action']}"] -= 1
        elif t == "HOLD_ENTERED":
            status = "HELD"
        elif t == "EVIDENCE" and d.get("source") in ("status", "support"):
            kind, correlated = d.get("kind"), d.get("correlated")
            if kind == "NOT_FOUND" or (kind in ("RECEIPT", "SUPPORT_REPLY") and not correlated):
                effects[d["effect_key"]]["not_found"] += 1
        elif t == "RECONCILED":
            eff = effects[d["effect_key"]]
            eff["status"], eff["receipt"] = "CONFIRMED", d["receipt"]
        elif t == "DECISION":
            applied = d["verdict"]["applied"]
            key = d["position"]["effect_key"]
            if applied == "VERIFY":
                usage[f"checks:{key}"] += 1
                if usage[f"directed_checks:{key}"] > 0:
                    usage[f"directed_checks:{key}"] -= 1
            elif applied == "CONTACT_SUPPORT" and profile.support and profile.support.budget is not None:
                usage[f"power:{profile.support.action}"] += 1
        elif t == "PRESERVED":
            status = "HELD"
        elif t == "DECISION_REQUEST":
            requests[d["question_id"]] = "OPEN"
            usage["authority_requests"] += 1
            status = "AWAITING_AUTHORITY"
        elif t == "AUTHORITY_ANSWER":
            requests[d["question_id"]] = "ANSWERED"
            if d["answer"] in ("APPROVE_ONCE", "REFUSE"):
                status = "OPEN"
        elif t == "AUTHORITY_GRANT":
            g = d["grant"]
            if g["kind"] == "EXTRA_CHECK":
                usage[f"extra_checks:{g['effect_key']}"] += 1
                usage[f"directed_checks:{g['effect_key']}"] += 1
                status = "HELD"
        elif t == "RESUMED":
            if status in ("HELD", "AWAITING_AUTHORITY"):
                status = "OPEN"
        elif t == "REVOKED":
            revoked = True
            requests = {q: ("CANCELLED" if s == "OPEN" else s) for q, s in requests.items()}
            status = "REVOKED"
        elif t == "MISSION_CLOSED":
            requests = {q: ("CANCELLED" if s == "OPEN" else s) for q, s in requests.items()}
            status = d["outcome"]
    return {"status": status, "revoked": revoked, "effects": effects, "usage": {k: v for k, v in usage.items() if v}, "requests": requests, "grants_consumed": sorted(grants_consumed)}


def store_projection(store, mission_id: str) -> dict[str, Any]:
    m = store.mission(mission_id)
    effects = {
        r["effect_key"]: {"action": r["action"], "operation_id": r["operation_id"], "status": r["status"], "receipt": r["receipt"], "not_found": r["not_found"], "attempts": r["attempts"]}
        for r in store.effects(mission_id)
    }
    requests = {r["question_id"]: r["status"] for r in store.read("SELECT question_id, status FROM requests WHERE mission=?", (mission_id,))}
    grants = sorted(r["id"] for r in store.read("SELECT id FROM grants WHERE mission=? AND consumed=1", (mission_id,)))
    return {"status": m["status"], "revoked": bool(m["revoked"]), "effects": effects, "usage": {k: v for k, v in store.usage(mission_id).items() if v}, "requests": requests, "grants_consumed": grants}


def state_replay(store, mission_id: str, profile: Profile) -> dict[str, Any]:
    replayed = fold(store.events(mission_id), profile)
    current = store_projection(store, mission_id)
    diffs = [k for k in replayed if replayed[k] != current[k]]
    return {"matches": not diffs, "differences": {k: {"replayed": replayed[k], "store": current[k]} for k in diffs}}


def decision_replay(events: list[Event], profile: Profile) -> dict[str, Any]:
    checked, mismatches = 0, []
    for e in events:
        if e.type != "DECISION":
            continue
        d = e.data
        pos = d["position"]
        paths = [PathState(**p) for p in pos["paths"]]
        ref = reference_path(paths, profile.hesitation.ladder)
        v = admit_path(d["proposal"]["path"], pos, d["read_digest"], d["now_digest"], on_refused=d.get("on_refused", profile.hesitation.on_refused_proposal))
        checked += 1
        if ref != pos["reference"] or v.__dict__ != d["verdict"]:
            mismatches.append({"seq": e.seq, "round": d["round"], "recorded": d["verdict"], "replayed": v.__dict__, "reference_recorded": pos["reference"], "reference_replayed": ref})
    return {"checked": checked, "matches": not mismatches, "mismatches": mismatches}


def policy_replay(events: list[Event], *, tau_star: float | None = None, ladder: list[str] | None = None, profile: Profile) -> list[dict[str, Any]]:
    """Ce que la référence aurait choisi, position par position, sous une autre politique. Aucun effet."""
    out = []
    for e in events:
        if e.type != "DECISION":
            continue
        pos = e.data["position"]
        tau = pos["tau_star"] if tau_star is None else tau_star
        paths = [PathState(**{**p, "reaches_tau": p["open"] and p["sigma"] >= tau if p["name"] != "PRESERVE" else False}) for p in pos["paths"]]
        alt = reference_path(paths, ladder or profile.hesitation.ladder)
        out.append({"round": e.data["round"], "recorded": e.data["verdict"]["applied"], "replayed": alt, "changed": alt != pos["reference"]})
    return out


def export_journal(store, mission_id: str) -> dict[str, Any]:
    m = store.mission(mission_id)
    return {
        "format": "hold.journal/v1",
        "mission": {"id": m["id"], "title": m["title"], "status": m["status"]},
        "profiles": {fp: json.loads(store.profile_json(fp)) for fp in {e.profile for e in store.events(mission_id)}},
        "events": [e.to_dict() for e in store.events(mission_id)],
    }
