"""Qualification du discerneur : on fait tourner les scénarios de démonstration et l'on compare,
tour par tour, le choix du discerneur à la référence calculée hors du modèle.

La sûreté ne dépend pas du résultat (le plancher tient) ; la qualification mesure l'habileté :
concordance, chemins fermés proposés, sur-sollicitation de l'autorité.
"""

from __future__ import annotations

from typing import Any

from .demo import SCENARIOS, run


def qualify(discerner: str = "llama", scenarios: list[str] | None = None, out=print) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    safety: list[dict[str, Any]] = []
    for name in scenarios or [s for s in SCENARIOS if s != "envelope"]:
        r = run(name, agent="scripted", discerner=discerner, relay="simulated", color=False, out=lambda _s: None)
        events = r["hab"].mission(r["mission"]).events()
        safety.append({"scenario": name, "bookings": r["bookings"], "chain_valid": r["chain_valid"], "status": r["status"]})
        for e in events:
            if e.type == "PROPOSAL":
                j = e.data["judgment"]
                rows.append({
                    "scenario": name, "round": e.data["round"], "reference": e.data["position"]["reference"],
                    "proposed": e.data["proposal"].get("path"), "error": e.data["proposal"].get("error"),
                    "duration_ms": e.data["proposal"].get("duration_ms"), **j,
                })
    n = len(rows)
    summary = {
        "decisions": n,
        "choice_concordance": sum(r["choice"] for r in rows),
        "perception_concordance": sum(r["perception"] for r in rows),
        "closed_path_proposals": sum(r["chose_closed_path"] for r in rows),
        "over_escalations": sum(r["over_escalation"] for r in rows),
        "model_errors": sum(1 for r in rows if r["error"]),
        "unsafe_outcomes": sum(1 for s in safety if s["bookings"] > 1 or not s["chain_valid"]),
    }
    out(f"Qualifying discerner \"{discerner}\" on {len(safety)} scenarios, {n} decisions")
    for r in rows:
        mark = "✔" if r["choice"] else "✘"
        out(f"  {mark} {r['scenario']:<14} round {r['round']} · reference {r['reference']:<16} proposed {str(r['proposed']):<16}"
            f"{' · closed path' if r['chose_closed_path'] else ''}{' · over-escalation' if r['over_escalation'] else ''}{' · error ' + r['error'] if r['error'] else ''}")
    out(f"Concordant choices {summary['choice_concordance']}/{n} · perception {summary['perception_concordance']}/{n} · "
        f"closed paths proposed {summary['closed_path_proposals']} · over-escalations {summary['over_escalations']} · model errors {summary['model_errors']}")
    out(f"Safety: {summary['unsafe_outcomes']} unsafe outcome (duplicate or invalid journal) over {len(safety)} scenarios")
    return {"summary": summary, "rows": rows, "safety": safety}
