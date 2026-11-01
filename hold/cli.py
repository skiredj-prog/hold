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
    from .replay import decision_replay, export_journal, policy_replay, state_replay
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
    sr = state_replay(store, a.mission, profile)
    dr = decision_replay(events, profile)
    print(f"Chain: {'valid' if chain.valid else 'INVALID at ' + str(chain.broken_at) + ' (' + str(chain.reason) + ')'} · {chain.count} events")
    print(f"State replay: {'identical' if sr['matches'] else 'DIFFERENT ' + ', '.join(sr['differences'])}")
    print(f"Decision replay: {dr['checked']} verdict(s), {'all reproduced' if dr['matches'] else str(len(dr['mismatches'])) + ' mismatch(es)'}")
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

    pq = sub.add_parser("qualify", help="qualifier un discerneur (Llama) contre la référence")
    pq.add_argument("--discerner", default="llama", choices=["llama", "reference"])

    pd = sub.add_parser("demo", help="le piège de la double réservation")
    pd.add_argument("--scenario", default="authority", choices=["lost-response", "support", "authority", "never", "envelope"])
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
    if a.cmd == "qualify":
        import os

        from .qualify import qualify

        os.environ.setdefault("HOLD_DEMO_TIMEOUT", "0.2")
        r = qualify(a.discerner)
        return 0 if r["summary"]["unsafe_outcomes"] == 0 else 1
    if a.cmd == "demo":
        from .demo import SCENARIOS, run, run_baseline
        from .testing import BOOKING, SyntheticHotel

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
