"""Rejeu à partir du seul journal, sans rappeler ni le modèle ni le fournisseur.

- state_replay  : reconstruit la projection (effets, compteurs, demandes, statut) et la compare au magasin.
- decision_replay : recalcule chaque verdict du plancher à partir des entrées enregistrées.
- policy_replay   : rejuge les positions enregistrées sous un autre τ* ou une autre échelle (contrefactuel).
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .canonical import digest
from .floor import admit_proposal
from .hesitation import PathState, boundary_position, reference_path
from .journal import Event
from .profile import Profile


class Fold:
    """Rebuilds the mission's state from its events alone, event by event, with the profile each event
    was recorded under. Used to recompute positions and verdicts instead of trusting the journal."""

    def __init__(self, profiles: Profile | dict[str, Profile]):
        self.profiles = profiles
        self.status = "OPEN"
        self.revoked = False
        self.effects: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, int] = defaultdict(int)
        self.requests: dict[str, str] = {}
        self.grants_consumed: set[str] = set()
        self.positions: dict[int, dict[str, Any]] = {}
        self.observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.no_response: dict[str, dict[str, Any]] = {}
        self.authority_reachable = False
        self.status_mismatches: list[dict[str, Any]] = []

    def profile(self, e: Event) -> Profile:
        return self.profiles if isinstance(self.profiles, Profile) else self.profiles[e.profile]

    def apply(self, e: Event) -> None:
        d, t, profile = e.data, e.type, self.profile(e)

        def budget(action: str) -> bool:
            p = profile.power(action)
            return bool(p and p.budget is not None)

        usage, effects = self.usage, self.effects
        if t == "RUNTIME_BOUND":
            self.authority_reachable = bool(d.get("authority_reachable"))
        elif t == "ACTION_ADMITTED":
            if d.get("kind") != "effect" and budget(d["action"]):
                usage[f"power:{d['action']}"] += 1
            if d.get("grant"):
                self.grants_consumed.add(d["grant"])
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
            if d["status"] == "UNKNOWN" and d["effect_key"] not in self.no_response:
                self.no_response[d["effect_key"]] = {"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": "core", "kind": "NO_RESPONSE", "text": d.get("reason")}
        elif t == "HOLD_ENTERED":
            self.status = "HELD"
        elif t == "EVIDENCE":
            self.observations[d["effect_key"]].append({"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": d.get("source"), "kind": d.get("kind"), "text": d.get("detail") or d.get("receipt") or d.get("reason")})
            if d.get("source") in ("status", "support"):
                kind, correlated = d.get("kind"), d.get("correlated")
                if kind == "NOT_FOUND" or (kind in ("RECEIPT", "SUPPORT_REPLY") and not correlated):
                    effects[d["effect_key"]]["not_found"] += 1
        elif t == "WAIT_ENDED":
            self.observations[d["effect_key"]].append({"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": "core", "kind": "WAITED", "text": f"Waited until {d.get('until')}; the position is read again."})
        elif t == "RECONCILED":
            eff = effects[d["effect_key"]]
            eff["status"], eff["receipt"] = "CONFIRMED", d["receipt"]
        elif t == "POSITION":
            self.positions[e.seq] = d["position"]
        elif t == "DECISION":
            applied = d["verdict"]["applied"]
            key = self.positions[d["position_seq"]]["effect_key"]
            if applied == "WAIT":
                usage[f"waits:{key}"] += 1
                usage[f"waited_s:{key}"] += d["verdict"]["wait_seconds"]
            elif applied == "VERIFY":
                usage[f"checks:{key}"] += 1
                if usage[f"directed_checks:{key}"] > 0:
                    usage[f"directed_checks:{key}"] -= 1
            elif applied == "CONTACT_SUPPORT" and profile.support and profile.support.budget is not None:
                usage[f"power:{profile.support.action}"] += 1
        elif t in ("PRESERVED", "WAIT_STARTED"):
            self.status = "HELD"
        elif t == "DECISION_REQUEST":
            self.requests[d["question_id"]] = "OPEN"
            usage["authority_requests"] += 1
            self.status = "AWAITING_AUTHORITY"
        elif t == "AUTHORITY_ANSWER":
            self.requests[d["question_id"]] = "ANSWERED"
            if d["answer"] == "REFUSE":
                self.status = "OPEN"
        elif t == "REQUEST_CLOSED":
            self.requests[d["question_id"]] = d["status"]
            self.status = d["mission_status"]
        elif t == "AUTHORITY_GRANT":
            g = d["grant"]
            if g["kind"] == "EXTRA_CHECK":
                usage[f"extra_checks:{g['effect_key']}"] += 1
                usage[f"directed_checks:{g['effect_key']}"] += 1
                self.status = "HELD"
            elif g["kind"] == "ENVELOPE_ONCE":
                self.status = "OPEN"
        elif t == "RESUMED":
            if self.status in ("HELD", "AWAITING_AUTHORITY"):
                self.status = "OPEN"
        elif t == "REVOKED":
            self.revoked = True
            self.requests = {q: ("CANCELLED" if s == "OPEN" else s) for q, s in self.requests.items()}
            self.status = "REVOKED"
        elif t == "MISSION_CLOSED":
            self.requests = {q: ("CANCELLED" if s == "OPEN" else s) for q, s in self.requests.items()}
            self.status = d["outcome"]
        if e.status is not None and e.status != self.status:
            self.status_mismatches.append({"seq": e.seq, "type": t, "recorded": e.status, "recomputed": self.status})

    def position(self, key: str, profile: Profile) -> dict[str, Any]:
        """The boundary position as the kernel must have computed it at this point of the history."""
        eff = self.effects[key]
        obs = list(self.observations[key][-8:])
        if key in self.no_response:
            obs.insert(0, self.no_response[key])
        return boundary_position(
            profile, effect_key=key, action=eff["action"], operation_id=eff["operation_id"], effect_state=eff["status"],
            not_found=eff["not_found"], usage=dict(self.usage), authority_available=self.authority_reachable,
            open_request=any(s == "OPEN" for s in self.requests.values()), revoked=self.revoked, observations=obs,
        ).as_dict()

    def state(self) -> dict[str, Any]:
        return {"status": self.status, "revoked": self.revoked, "effects": self.effects, "usage": {k: v for k, v in self.usage.items() if v},
                "requests": self.requests, "grants_consumed": sorted(self.grants_consumed)}


def fold(events: list[Event], profile: Profile | dict[str, Profile]) -> dict[str, Any]:
    f = Fold(profile)
    for e in events:
        f.apply(e)
    return f.state()


def verify_decisions(events: list[Event], profiles: Profile | dict[str, Profile]) -> dict[str, Any]:
    """Independent verification, from the history alone: each position is recomputed from the events
    before it and the profile in force, then each verdict is recomputed from that position and the
    recorded proposal. Nothing the journal asserts about positions or verdicts is taken as given."""
    f = Fold(profiles)
    pos_checked, pos_mismatch, dec_checked, dec_mismatch = 0, [], 0, []
    recomputed: dict[int, dict[str, Any]] = {}
    for e in events:
        if e.type == "POSITION":
            mine = f.position(e.data["effect_key"], f.profile(e))
            recomputed[e.seq] = mine
            pos_checked += 1
            if mine != e.data["position"]:
                diff = sorted(k for k in mine if mine[k] != e.data["position"].get(k))
                pos_mismatch.append({"seq": e.seq, "round": e.data["round"], "fields": diff})
        elif e.type == "DECISION":
            d = e.data
            pos = recomputed.get(d["position_seq"])
            prof = f.profile(e)
            dec_checked += 1
            if pos is None:
                dec_mismatch.append({"seq": e.seq, "error": "no recomputed position"})
            else:
                v = admit_proposal(d["proposal"], pos, digest(pos), d["now_digest"], final=d["final"], on_refused=d.get("on_refused", prof.hesitation.on_refused_proposal))
                problems = []
                if digest(pos) != d["read_digest"]:
                    problems.append("the gate read a position the history does not imply")
                if v.__dict__ != d["verdict"] or v.outcome != d["outcome"]:
                    problems.append("verdict differs")
                if problems:
                    dec_mismatch.append({"seq": e.seq, "round": d["round"], "problems": problems, "recorded": d["verdict"], "recomputed": v.__dict__})
        f.apply(e)
    return {
        "positions": {"checked": pos_checked, "matches": not pos_mismatch, "mismatches": pos_mismatch},
        "decisions": {"checked": dec_checked, "matches": not dec_mismatch, "mismatches": dec_mismatch},
        "status": {"matches": not f.status_mismatches, "mismatches": f.status_mismatches},
        "state": f.state(),
    }


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


def decision_replay(events: list[Event], profile: Profile | dict[str, Profile]) -> dict[str, Any]:
    """`profile`: the mission's profile, or a map fingerprint -> profile when the mission adopted a revision."""
    checked, mismatches = 0, []
    positions: dict[int, dict[str, Any]] = {}
    for e in events:
        if e.type == "POSITION":
            positions[e.seq] = e.data["position"]
            continue
        if e.type != "DECISION":
            continue
        if isinstance(profile, dict):
            if e.profile not in profile:
                mismatches.append({"seq": e.seq, "round": e.data["round"], "error": f"profile {e.profile[:12]} missing from the journal"})
                continue
            prof = profile[e.profile]
        else:
            prof = profile
        d = e.data
        pos = positions.get(d["position_seq"])
        if pos is None:
            mismatches.append({"seq": e.seq, "round": d["round"], "error": f"position event {d['position_seq']} missing"})
            continue
        paths = [PathState(**p) for p in pos["paths"]]
        ref = reference_path(paths, prof.hesitation.ladder)
        v = admit_proposal(d["proposal"], pos, d["read_digest"], d["now_digest"], final=d["final"], on_refused=d.get("on_refused", prof.hesitation.on_refused_proposal))
        checked += 1
        if ref != pos["reference"] or v.__dict__ != d["verdict"] or v.outcome != d["outcome"]:
            mismatches.append({"seq": e.seq, "round": d["round"], "recorded": d["verdict"], "replayed": v.__dict__, "reference_recorded": pos["reference"], "reference_replayed": ref})
    return {"checked": checked, "matches": not mismatches, "mismatches": mismatches}


def policy_replay(events: list[Event], *, tau_star: float | None = None, ladder: list[str] | None = None, profile: Profile) -> list[dict[str, Any]]:
    """Ce que la référence aurait choisi, position par position, sous une autre politique. Aucun effet."""
    out = []
    positions: dict[int, dict[str, Any]] = {}
    for e in events:
        if e.type == "POSITION":
            positions[e.seq] = e.data["position"]
            continue
        if e.type != "DECISION" or e.data["verdict"]["applied"] is None:
            continue
        pos = positions[e.data["position_seq"]]
        tau = pos["tau_star"] if tau_star is None else tau_star
        paths = [PathState(**{**p, "reaches_tau": p["open"] and p["sigma"] >= tau if p["name"] != "PRESERVE" else False}) for p in pos["paths"]]
        alt = reference_path(paths, ladder or profile.hesitation.ladder)
        out.append({"round": e.data["round"], "recorded": e.data["verdict"]["applied"], "replayed": alt, "changed": alt != pos["reference"]})
    return out


def journal_profiles(store, events: list[Event]) -> dict[str, Profile]:
    """Every profile revision the mission wore, as recorded in the store (fingerprint -> profile)."""
    from .profile import load_profile

    return {fp: load_profile(json.loads(store.profile_json(fp))) for fp in {e.profile for e in events}}


def export_journal(store, mission_id: str) -> dict[str, Any]:
    m = store.mission(mission_id)
    return {
        "format": "hold.journal/v1",
        "mission": {"id": m["id"], "title": m["title"], "status": m["status"]},
        "profiles": {fp: json.loads(store.profile_json(fp)) for fp in {e.profile for e in store.events(mission_id)}},
        "events": [e.to_dict() for e in store.events(mission_id)],
        "store": store_projection(store, mission_id),  # what the store says, to compare with what the events imply
    }
