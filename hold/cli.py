"""Ligne de commande HOLD.

  hold profile validate|fingerprint|sign|schema <fichier>
  hold missions <base>
  hold journal verify|export|policy <base> <mission>
  hold answer <base> <mission> <question_id> <CODE>
  hold studio [--db hold.db] [--port 8787]
  hold demo [--scenario authority] [--agent scripted|llama] [--discerner reference|llama] [--relay simulated|whatsapp|console] [--baseline]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pydantic import ValidationError


def _profile_cmd(a: argparse.Namespace) -> int:
    from .profile import dump_profile, json_schema, load_profile, sign

    if a.action == "schema":
        print(json.dumps(json_schema(), ensure_ascii=False, indent=2))
        return 0
    try:
        p = load_profile(a.file)
    except (ValidationError, ValueError) as e:
        print(f"Invalid profile: {e}", file=sys.stderr)
        return 2
    if a.action == "fingerprint":
        print(p.fingerprint)
    elif a.action == "validate":
        print(f"✔ {p.id} rev. {p.revision} · {p.title}")
        print(f"  fingerprint {p.fingerprint}")
        print(f"  authority {p.authority.name} ({p.authority.channel}) · tau*={p.hesitation.tau_star} · ladder {' → '.join(p.hesitation.ladder)}")
        for pw in p.powers:
            env = ""
            if pw.envelope and pw.envelope.max_amount:
                env = f" · ceiling {pw.envelope.max_amount.amount:g} {pw.envelope.max_amount.currency}"
            print(f"  - {pw.action} [{pw.kind}]{' budget ' + str(pw.budget) if pw.budget is not None else ''}{env}")
        print(f"  signed: {'yes, by ' + p.signature.by if p.signature else 'no'}")
    elif a.action == "sign":
        if not a.secret or not a.by:
            print("--secret and --by are required", file=sys.stderr)
            return 2
        out = Path(a.out or a.file)
        dump_profile(sign(p, a.secret, a.by), out)
        print(f"Profile signed by {a.by} → {out}")
    return 0


def _journal_cmd(a: argparse.Namespace) -> int:
    from .profile import load_profile
    from .replay import export_journal, journal_profiles, policy_replay, state_replay, verify_decisions
    from .journal import verify_chain
    from .store import Store

    store = Store(a.db)
    row = store.mission(a.mission)
    if not row:
        print("Unknown mission", file=sys.stderr)
        return 2
    profile = load_profile(json.loads(store.profile_json(row["profile_fp"])))
    events = store.events(a.mission)
    if a.action == "export":
        data = json.dumps(export_journal(store, a.mission), ensure_ascii=False, indent=2)
        (Path(a.out).write_text(data, encoding="utf-8") if a.out else print(data))
        return 0
    if a.action == "policy":
        for r in policy_replay(events, tau_star=a.tau, profile=profile):
            print(f"  round {r['round']}: recorded {r['recorded']:<16} replayed {r['replayed']:<16}{' ← changes' if r['changed'] else ''}")
        return 0
    chain = verify_chain(events)
    sr = state_replay(store, a.mission, journal_profiles(store, events))
    vd = verify_decisions(events, journal_profiles(store, events))
    ok = lambda r: "all recomputed identical" if r["matches"] else f"{len(r['mismatches'])} mismatch(es)"  # noqa: E731
    print(f"Chain: {'valid' if chain.valid else 'INVALID at ' + str(chain.broken_at) + ' (' + str(chain.reason) + ')'} · {chain.count} events")
    print(f"Positions recomputed from the history: {vd['positions']['checked']}, {ok(vd['positions'])}")
    print(f"Verdicts recomputed from those positions: {vd['decisions']['checked']}, {ok(vd['decisions'])}")
    print(f"Mission status recomputed event by event: {ok(vd['status'])}")
    print(f"Final state vs store: {'identical' if sr['matches'] else 'DIFFERENT ' + ', '.join(sr['differences'])}")
    print("Not verifiable from the journal alone: the provider's register, the authority's identity (checked at runtime), model outputs (re-run the recorded model), completeness, clock.")
    return 0 if chain.valid and sr["matches"] and dr["matches"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hold", description="HOLD — prêt-à-porter habilitation for autonomous agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("profile", help="valider, signer, exporter le schéma d'un profil")
    pp.add_argument("action", choices=["validate", "fingerprint", "sign", "schema"])
    pp.add_argument("file", nargs="?")
    pp.add_argument("--secret")
    pp.add_argument("--by")
    pp.add_argument("--out")

    pm = sub.add_parser("missions", help="lister les missions d'une base")
    pm.add_argument("db")

    pj = sub.add_parser("journal", help="vérifier, exporter ou rejouer un journal")
    pj.add_argument("action", choices=["verify", "export", "policy"])
    pj.add_argument("db")
    pj.add_argument("mission")
    pj.add_argument("--out")
    pj.add_argument("--tau", type=float)

    pa = sub.add_parser("answer", help="répondre à une Decision Request depuis la console")
    pa.add_argument("db")
    pa.add_argument("mission")
    pa.add_argument("question_id")
    pa.add_argument("code")

    ps = sub.add_parser("studio", help="servir HOLD Studio et le webhook")
    ps.add_argument("--db", default="hold.db")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8787)

    pb = sub.add_parser("bench", help="the frozen A/B/C/C2/D comparison")
    pb.add_argument("action", choices=["freeze", "check", "run", "rerun", "evaluate", "smoke", "probe"])
    pb.add_argument("--reps", type=int, default=3, help="probe: repetitions per cell (seeds 42, 43, …)")
    pb.add_argument("--probes", default="P1,P2,P3,F", help="probe: which micro-tests (P1,P2,P3,F)")
    pb.add_argument("--systems", default="neutral,hold,hold-min", help="probe: which system prompts (neutral,hold,hold-min)")
    pb.add_argument("--contract", default="hold", choices=["hold", "hold-min"], help="smoke: the HOLD contract C-llama reads (diagnostic)")
    pb.add_argument("--frozen", default="bench/protocol.frozen.json")
    pb.add_argument("--conditions", default="C,C2,X", help="comma separated: A,B,C-llama,C,C2,D,X")
    pb.add_argument("--model", help="Ollama model for A, B, C-llama and D (e.g. llama3.2:3b, llama3.1:8b)")
    pb.add_argument("--out", default="bench/results/run")
    pb.add_argument("--results", nargs="*", help="results.json files to evaluate together")

    pd = sub.add_parser("demo", help="le piège de la double réservation")
    pd.add_argument("--scenario", default="authority", choices=["lost-response", "support", "authority", "never", "envelope", "a5-recovery"])
    pd.add_argument("--agent", default="scripted", choices=["scripted", "llama"])
    pd.add_argument("--discerner", default="reference", choices=["reference", "llama"])
    pd.add_argument("--relay", default="simulated", choices=["simulated", "whatsapp", "console"])
    pd.add_argument("--db", default=":memory:")
    pd.add_argument("--serve", type=int, help="port du serveur (Studio + webhook) pendant la démo")
    pd.add_argument("--contact", help="numéro WhatsApp de l'autorité (remplace celui du profil)")
    pd.add_argument("--baseline", action="store_true", help="exécuter d'abord le témoin sans HOLD")
    pd.add_argument("--no-color", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "profile":
        if a.action != "schema" and not a.file:
            ap.error("fichier de profil requis")
        return _profile_cmd(a)
    if a.cmd == "journal":
        return _journal_cmd(a)
    if a.cmd == "missions":
        from .store import Store

        for r in Store(a.db).missions():
            print(f"{r['id']}  {r['status']:<20} {r['title']}")
        return 0
    if a.cmd == "answer":
        from .profile import load_profile
        from .runtime import Habilitation
        from .store import Store

        store = Store(a.db)
        row = store.mission(a.mission)
        if not row:
            print("Unknown mission", file=sys.stderr)
            return 2
        hab = Habilitation(load_profile(json.loads(store.profile_json(row["profile_fp"]))), store=store)
        print(json.dumps(hab.mission(a.mission).answer(a.question_id, a.code, channel="console", evidence={}), ensure_ascii=False))
        return 0
    if a.cmd == "studio":
        from .server import serve
        from .store import Store

        print(f"HOLD Studio : http://{a.host}:{a.port}")
        serve(Store(a.db), [], host=a.host, port=a.port, background=False)
        return 0
    if a.cmd == "bench":
        from . import bench

        if a.action == "freeze":
            Path(a.frozen).parent.mkdir(parents=True, exist_ok=True)
            f = bench.freeze(a.frozen)
            print(f"Protocol frozen: {a.frozen}\nSHA-256 {f['sha256']}")
            return 0
        if a.action == "check":
            ok, problems = bench.check_freeze(a.frozen)
            print("Frozen protocol matches the code." if ok else "\n".join(problems))
            return 0 if ok else 1
        if a.action == "run":
            conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
            if any(c in ("A", "B", "C-llama", "D") for c in conds) and not a.model:
                print("--model is required for A, B, C-llama and D", file=sys.stderr)
                return 2
            r = bench.run(conds, model=a.model, frozen=a.frozen, out_dir=a.out)
            print(f"Raw runs and results: {a.out}")
            ev = bench.evaluate(r["results"], expected_protocol=r["protocol_sha256"], strict=True)
            print(json.dumps(ev, ensure_ascii=False, indent=1))
            return 0 if ev["identity"]["valid"] else 1
        if a.action == "rerun":
            ok, problems = bench.check_freeze(a.frozen)
            if not ok:
                print("Refusing to re-run: " + "; ".join(problems), file=sys.stderr)
                return 1
            target = Path(a.out) / "results.json"
            print(json.dumps(bench.rerun_failed(target, model=a.model), ensure_ascii=False, indent=1))
            return 0
        if a.action == "probe":
            # A6: diagnostic micro-tests asked by the PMO (progression, fidelity), real model, simplified interface: never evidence
            if not a.model:
                print("--model is required", file=sys.stderr)
                return 2
            from .probe import run_probes

            out = Path(a.out if a.out != "bench/results/run" else f"bench/results/probe-{a.model.replace(':', '-')}-{time.strftime('%Y%m%d-%H%M%S')}")
            out.mkdir(parents=True, exist_ok=True)
            r = run_probes(a.model, reps=a.reps, systems_=[x.strip() for x in a.systems.split(",") if x.strip()],
                           probes=[x.strip() for x in a.probes.split(",") if x.strip()])
            (out / "probe.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\nPROBES ({a.model}, {a.reps} seeds per cell, {r['elapsed_s']} s): diagnostic only, never evidence.")
            for section, rows in r["summary"].items():
                if not rows:
                    continue
                print(f"\n{section}")
                for k, v in rows.items():
                    print(f"  {k:<26} {v}")
            print(f"\nEvery answer and trace: {out / 'probe.json'}")
            return 0
        if a.action == "smoke":
            # A6: real model, DEV scenarios, one repetition, before any freeze. Calibration only: never evidence.
            conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
            if a.conditions == "C,C2,X":  # the bench default: for a smoke, the pre-qualification condition only
                conds = list(bench.PREQUAL["conditions"])
            if not a.model:
                print("--model is required", file=sys.stderr)
                return 2
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out = a.out if a.out != "bench/results/run" else f"bench/results/smoke-{a.model.replace(':', '-')}-{a.contract}-{stamp}"
            started = time.monotonic()
            lines = bench.smoke(conds, model=a.model, out_dir=out, contract=a.contract)
            print(f"SMOKE (DEV scenarios, one repetition, contract {a.contract}): calibration only, never evidence.")
            for x in lines:
                print(f"\n{x['run']}  turns={x['turns']} not_executed={x['not_executed']} created={x['created']} status={x['status']}" + (f" ERRORS={x['errors']}" if x["errors"] else ""))
                print("  " + (" > ".join(x["steps"]) or "(no call reached the tools)"))
                print("  last: " + (x["last_message"] or "(none)"))
            pq = bench.prequalify(out) if "C-llama" in conds else None
            Path(out, "smoke.json").write_text(json.dumps({"runs": lines, "prequalification": pq}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\nRaw runs and smoke.json: {out}  ({len(lines)} runs, {time.monotonic() - started:.0f} s)")
            if pq is None:
                return 0
            print(f"\nPRE-QUALIFICATION: {pq['verdict']}  · continued after a block: {pq['continued_after_block']}")
            for reason in pq["failed"]:
                print("  - " + reason)
            return 0 if pq["verdict"] == "GO" else 1
        if a.action == "evaluate":
            # Qualification path: the anchor first. The designated frozen file must match its own SHA-256 and the code
            # that runs (the same check as bench check / run / rerun); then strict identity of every row against it.
            # Rows are taken as recorded; nothing is filled in from the file header.
            ok, problems = bench.check_freeze(a.frozen)
            if not ok:
                print("Refused: the designated frozen protocol is not a valid anchor: " + "; ".join(problems), file=sys.stderr)
                return 1
            frozen_sha = json.loads(Path(a.frozen).read_text(encoding="utf-8"))["sha256"]
            rows = [x for f in a.results or [] for x in json.loads(Path(f).read_text(encoding="utf-8"))["results"]]
            ev = bench.evaluate(rows, expected_protocol=frozen_sha, strict=True)
            # the digest of the anchor that was validated, to be compared with the one in the independent register
            print(json.dumps({"anchor": {"frozen": a.frozen, "sha256": frozen_sha}} | ev, ensure_ascii=False, indent=1))
            if not ev["identity"]["valid"]:
                print("Refused as evidence: " + "; ".join(ev["identity"]["problems"][:5]) + (" …" if len(ev["identity"]["problems"]) > 5 else ""), file=sys.stderr)
                return 1
            return 0
    if a.cmd == "demo":
        from .demo import SCENARIOS, run, run_baseline
        from .testing import BOOKING, SyntheticHotel

        if a.scenario == "a5-recovery":
            from .demo import demonstrate_a5

            r = demonstrate_a5(color=not a.no_color)
            return 0 if r["score"]["completed"] and not r["score"]["violations"] else 1
        if a.baseline:
            conf = SCENARIOS[a.scenario]
            run_baseline(SyntheticHotel(timeout_s=0.5, **{k: v for k, v in conf["hotel"].items() if k == "visible_after"}), dict(BOOKING))
            print()
        r = run(a.scenario, agent=a.agent, discerner=a.discerner, relay=a.relay, store=a.db, serve_port=a.serve, contact=a.contact, color=not a.no_color)
        return 0 if r["chain_valid"] and r["bookings"] <= 1 else 1
    return 0


def entry() -> None:
    try:
        sys.exit(main())
    except BrokenPipeError:  # sortie coupée (| head) : se taire proprement
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    entry()
