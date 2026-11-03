"""Preflight for the real WhatsApp relay: python -m hold.whatsapp_check [--contact 2126XXXXXXXX] [--url https://….trycloudflare.com] [--ping]

Checks, in order, what has to be right before `hold demo --relay whatsapp` can reach the authority's phone:
  1. the four WHATSAPP_* variables are set in this shell (values are never printed);
  2. the authority's number is in the format WhatsApp expects (international, digits only);
  3. the access token and the phone number ID are accepted by the Graph API;
  4. with --url: the running HOLD server is reachable through the tunnel, answers Meta's webhook
     verification with the same verify token, and refuses an unsigned event;
  5. with --ping: Meta accepts a text message to the authority (accepted is not delivered: look at the phone).

Exit code 0 when nothing failed. This module is outside the E1-A6 frozen perimeter (bench.CODE): it only
reads the relay's configuration (hold.relay.whatsapp.WhatsAppConfig) and sends what is asked.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from typing import Any

import httpx

from .relay.whatsapp import WhatsAppConfig, mask

NUMBER = re.compile(r"^[1-9]\d{7,14}$")  # same rule as the profile validator

FIX = {
    190: "The access token is invalid or expired. The temporary token from WhatsApp > API Setup lasts about 24 h: generate a new one and set WHATSAPP_ACCESS_TOKEN again.",
    10: "Permission denied: the token lacks whatsapp_business_messaging, or the number is not registered in this WhatsApp Business Account.",
    100: "Invalid parameter or unknown object: check WHATSAPP_PHONE_NUMBER_ID (the Phone number ID from API Setup, not the WhatsApp Business Account ID).",
    131030: "The recipient is not in the test number's allowed list: WhatsApp > API Setup > 'To' > Manage phone number list, add the number and enter the code it receives.",
    131047: "Outside the 24 h window: from the authority's phone, send any WhatsApp message to the business number, then run this again.",
    131026: "Message undeliverable: the number must have WhatsApp, an up-to-date app, and have accepted WhatsApp's terms.",
    133010: "The business phone number is not registered with the Cloud API.",
    131009: "A parameter value is invalid (often the 'to' number).",
    368: "The account is temporarily restricted by Meta for a policy reason.",
}


class Report:
    def __init__(self, out=sys.stdout):
        self.out, self.failed = out, 0

    def line(self, status: str, what: str, detail: str = "") -> None:
        if status == "FAIL":
            self.failed += 1
        self.out.write(f"{status:<5} {what}" + (f"\n      {detail}" if detail else "") + "\n")


def graph_error(r: httpx.Response) -> tuple[int | None, str]:
    try:
        err = (r.json() or {}).get("error") or {}
    except ValueError:
        return None, f"HTTP {r.status_code}"
    code = err.get("code")
    msg = " — ".join(x for x in (err.get("message"), (err.get("error_data") or {}).get("details")) if x) or f"HTTP {r.status_code}"
    return code, msg


def explain(code: int | None, msg: str) -> str:
    return f"{code} {msg}" + (f"\n      Fix: {FIX[code]}" if code in FIX else "")


def check_env(cfg: WhatsAppConfig, rep: Report) -> None:
    for name, value, role in (
        ("WHATSAPP_ACCESS_TOKEN", cfg.access_token, "send"),
        ("WHATSAPP_PHONE_NUMBER_ID", cfg.phone_number_id, "send"),
        ("WHATSAPP_APP_SECRET", cfg.app_secret, "receive: checks Meta's signature on every event"),
        ("WHATSAPP_VERIFY_TOKEN", cfg.verify_token, "receive: any string you choose, typed again in the webhook settings"),
    ):
        if value:
            rep.line("PASS", f"{name} is set ({len(value)} characters)")
        else:
            rep.line("FAIL", f"{name} is not set in this shell", f"Needed to {role}. PowerShell: $env:{name} = \"...\" (in the same window as hold demo).")
    if cfg.phone_number_id and not cfg.phone_number_id.isdigit():
        rep.line("FAIL", "WHATSAPP_PHONE_NUMBER_ID should be digits only", "Copy the Phone number ID shown under the 'From' number in WhatsApp > API Setup.")
    rep.line("INFO", f"Graph API {cfg.graph_version}" + (f", template fallback '{cfg.template_name}' ({cfg.template_lang})" if cfg.template_name else ", no template fallback (interactive messages need the 24 h window)"))


def check_contact(contact: str | None, rep: Report) -> bool:
    if not contact:
        rep.line("SKIP", "authority number (pass --contact 2126XXXXXXXX)")
        return False
    c = contact.strip()
    if NUMBER.match(c):
        hint = " (Morocco: drop the 0 after 212)" if c.startswith("2120") else ""
        rep.line("FAIL" if hint else "PASS", f"authority number {mask(c)}" + (" is not in international form" + hint if hint else " is in international form"))
        return not hint
    digits = re.sub(r"\D", "", c)
    if c.startswith("+") or c.startswith("00"):
        fixed = digits[2:] if c.startswith("00") else digits
        rep.line("FAIL", "authority number: digits only, no '+' or '00'", f"Use {mask(fixed)}.")
    else:
        rep.line("FAIL", "authority number: international form, digits only, 8 to 15 digits", "Example for Morocco: 212612345678.")
    return False


def check_graph(cfg: WhatsAppConfig, client: httpx.Client, rep: Report) -> bool:
    if not cfg.can_send:
        rep.line("SKIP", "Graph API (token and phone number ID not set)")
        return False
    url = f"{cfg.api_base}/{cfg.graph_version}/{cfg.phone_number_id}"
    try:
        r = client.get(url, params={"fields": "display_phone_number,verified_name,quality_rating"}, headers={"authorization": f"Bearer {cfg.access_token}"})
    except httpx.HTTPError as e:
        rep.line("FAIL", "Graph API unreachable", f"{type(e).__name__}: {e}")
        return False
    if r.status_code < 300:
        d = r.json()
        rep.line("PASS", f"token and phone number ID accepted: {d.get('display_phone_number', '?')} ({d.get('verified_name', '?')}), quality {d.get('quality_rating', '?')}")
        return True
    rep.line("FAIL", "Graph API refused the token or the phone number ID", explain(*graph_error(r)))
    return False


def check_server(base: str, cfg: WhatsAppConfig, client: httpx.Client, rep: Report) -> None:
    base = base.rstrip("/")
    try:
        h = client.get(f"{base}/api/health")
        wa = (h.json() or {}).get("whatsapp", {}) if h.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        rep.line("FAIL", f"HOLD server not reachable at {base}", f"{type(e).__name__}: {e}. Is hold demo running with --serve 8787, and the tunnel pointing to http://localhost:8787?")
        return
    if wa is None:
        rep.line("FAIL", f"{base}/api/health answered HTTP {h.status_code}", "The URL must reach HOLD's server (cloudflared tunnel --url http://localhost:8787).")
        return
    mode = wa.get("mode")
    if mode == "cloud":
        rep.line("PASS", "HOLD server reachable; relay mode 'cloud' (sends through the Graph API)")
    elif mode is None:
        rep.line("INFO", "HOLD server reachable, without a relay (hold studio): enough to verify the webhook in Meta's console; hold demo --relay whatsapp will send")
    else:
        rep.line("FAIL", f"HOLD server reachable; relay mode {mode!r}", "Start it with --relay whatsapp, in a shell where WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are set.")
    if not wa.get("receive"):
        rep.line("FAIL", "the server cannot receive", "Set WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN in the shell that runs hold demo.")
    challenge = secrets.token_hex(6)
    v = client.get(f"{base}/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": cfg.verify_token, "hub.challenge": challenge})
    if v.status_code == 200 and v.text == challenge:
        rep.line("PASS", "webhook verification answers like Meta expects", f"In Meta's console (WhatsApp > Configuration): Callback URL {base}/api/whatsapp/webhook, same verify token, subscribe to 'messages'.")
    elif v.status_code == 403:
        rep.line("FAIL", "webhook verification refused: the server's WHATSAPP_VERIFY_TOKEN differs from this shell's")
    else:
        rep.line("FAIL", f"webhook verification answered HTTP {v.status_code}", "404: the server has no WHATSAPP_VERIFY_TOKEN.")
    u = client.post(f"{base}/api/whatsapp/webhook", content=b"{}", headers={"content-type": "application/json"})
    if u.status_code == 401:
        rep.line("PASS", "an unsigned event is refused (signature checked with the app secret)")
    else:
        rep.line("FAIL", f"an unsigned event got HTTP {u.status_code}, expected 401", "503: the server has no WHATSAPP_APP_SECRET.")


def ping(cfg: WhatsAppConfig, contact: str, client: httpx.Client, rep: Report) -> None:
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": contact, "type": "text",
            "text": {"preview_url": False, "body": "HOLD preflight: if you can read this, the 24 h window is open and HOLD can send you Decision Requests."}}
    try:
        r = client.post(f"{cfg.api_base}/{cfg.graph_version}/{cfg.phone_number_id}/messages", headers={"authorization": f"Bearer {cfg.access_token}"}, json=body)
    except httpx.HTTPError as e:
        rep.line("FAIL", "ping: Graph API unreachable", f"{type(e).__name__}: {e}")
        return
    mid = (((r.json() if r.content else {}) or {}).get("messages") or [{}])[0].get("id") if r.status_code < 300 else None
    if mid:
        rep.line("PASS", f"ping accepted by Meta for {mask(contact)} (id …{mid[-8:]})",
                 "Accepted is not delivered: check the phone. Nothing within a minute usually means the 24 h window is closed: "
                 "send any message from that phone to the business number, then ping again.")
    else:
        rep.line("FAIL", f"ping refused for {mask(contact)}", explain(*graph_error(r)))


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None, client: httpx.Client | None = None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog="python -m hold.whatsapp_check", description="Check the WhatsApp relay before a live run.")
    ap.add_argument("--contact", help="the authority's WhatsApp number, international, digits only (e.g. 212612345678)")
    ap.add_argument("--url", help="public HTTPS URL of the running HOLD server (the tunnel), to check the webhook")
    ap.add_argument("--ping", action="store_true", help="send one text message to --contact")
    a = ap.parse_args(argv)
    cfg = WhatsAppConfig.from_env(env if env is not None else dict(os.environ))
    client = client or httpx.Client(timeout=10)
    rep = Report(out)
    check_env(cfg, rep)
    contact_ok = check_contact(a.contact, rep)
    graph_ok = check_graph(cfg, client, rep)
    if a.url:
        check_server(a.url, cfg, client, rep)
    else:
        rep.line("SKIP", "webhook (pass --url https://….trycloudflare.com while hold demo --relay whatsapp --serve 8787 runs)")
    if a.ping:
        if contact_ok and graph_ok:
            ping(cfg, a.contact.strip(), client, rep)
        else:
            rep.line("SKIP", "ping (needs a valid --contact and an accepted token)")
    rep.out.write(("Ready." if not rep.failed else f"{rep.failed} check(s) failed.") + "\n")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
