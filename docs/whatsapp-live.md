# Live WhatsApp: from a Meta test number to a tap that resolves a mission

The relay is complete in code (`hold/relay/whatsapp.py`): an interactive message with three reply buttons, HMAC-checked webhook (`X-Hub-Signature-256`), duplicate deliveries ignored, `STOP` to revoke, a template fallback outside the 24 h window. This page gets it working against Meta's Cloud API with the free test number. Budget: about one hour if nothing goes wrong. Until a live tap has resolved a mission and the journal shows it, present the relay as simulated.

What you need: a Meta developer account, the authority's phone with WhatsApp, `pip install -e ".[server]"`, and a tunnel that gives a public HTTPS URL (`cloudflared`; on Windows `winget install --id Cloudflare.cloudflared`).

## 1. Meta side (developers.facebook.com)

1. **Create the app.** My Apps > Create app, attach a business portfolio, add the **WhatsApp** product ("Set up").
2. **WhatsApp > API Setup.** Note the test number in **From**, its **Phone number ID** (digits, not the WhatsApp Business Account ID), and generate a **temporary access token** (it expires after about 24 h).
3. **Allowed recipients.** In **To**, choose **Manage phone number list**, add the authority's number and enter the code WhatsApp sends to it. Up to 5 numbers.
4. **App secret.** App settings > Basic > App secret > Show. HOLD uses it to check that every webhook event comes from Meta.
5. **Verify token.** Any string you choose, for example `hold-verify-2026`. You will type it twice: in your shell and in Meta's webhook settings.

## 2. Your shell (same window for every command below)

```powershell
$env:WHATSAPP_ACCESS_TOKEN    = "EAAG..."           # temporary token from API Setup
$env:WHATSAPP_PHONE_NUMBER_ID = "1234567890..."     # Phone number ID, digits
$env:WHATSAPP_APP_SECRET      = "..."               # App settings > Basic
$env:WHATSAPP_VERIFY_TOKEN    = "hold-verify-2026"  # your choice
python -m hold.whatsapp_check --contact 2126XXXXXXXX
```

(bash: `export WHATSAPP_ACCESS_TOKEN=...` and so on.) The number is international, digits only: no `+`, no `00`, and for Morocco no `0` after `212`. Every line should read `PASS`, except the webhook, which is checked in step 3.

**Open the 24 h window.** From the authority's phone, send any WhatsApp message (for example "hi") to the test number. Interactive messages are only delivered within 24 h of the recipient's last message. Then:

```powershell
python -m hold.whatsapp_check --contact 2126XXXXXXXX --ping
```

The phone should receive "HOLD preflight: …". Meta accepting a message does not mean it was delivered, so look at the phone. If nothing arrives within a minute, the window is closed or the number is not in the allowed list.

## 3. Webhook

Safer variant: run the mission with `python -m experience --relay whatsapp …` and tunnel only its webhook gateway (`python -m experience.gateway`, port 8788), so nothing but the signed webhook is reachable from outside. See [experience/README.md](../experience/README.md). The steps below tunnel the HOLD server itself; its mission routes refuse requests that arrive through the tunnel.


Terminal A, the tunnel. Note the `https://….trycloudflare.com` URL it prints; it changes each time cloudflared restarts.

```powershell
cloudflared tunnel --url http://localhost:8787
```

Terminal B, the server (with the four variables set):

```powershell
hold studio --db live.db --port 8787
python -m hold.whatsapp_check --url https://xxxx.trycloudflare.com    # in a third window, same variables
```

In Meta's console, open **WhatsApp > Configuration > Webhook**:
- **Callback URL**: `https://xxxx.trycloudflare.com/api/whatsapp/webhook`
- **Verify token**: the same string as your variable.
- Click **Verify and save**.
- Under **Webhook fields**, subscribe to **messages**.

Mission data stays local: requests that come through the tunnel get 403 on the mission routes (the guard looks at the forwarding headers cloudflared adds).

## 4. The live run

Stop `hold studio` (Ctrl+C) and leave the tunnel running. In terminal B:

```powershell
hold demo --relay whatsapp --contact 2126XXXXXXXX --serve 8787 --scenario authority --db live.db
```

The agent books, the provider does not answer, HOLD checks twice, then sends the Decision Request to the phone (three buttons, in the authority's language). The demo waits 10 minutes. Tap **Autoriser 1 contrôle** (Allow 1 more check). The check finds the receipt, nothing is repeated, and the mission closes `CLOSED_COMPLETED`.

Evidence to keep: a screen recording of the phone (message, tap) and of Studio at http://127.0.0.1:8787 → Read a mission. Studio shows the journal re-verified in the browser, and relay messages in mode `cloud` with their WhatsApp message ids. `hold missions live.db`, then `python -m hold.journal_verify live.db <mission>`, re-checks the journal offline.

## When it fails

| Symptom | Cause and fix |
|---|---|
| `190` | Token expired or wrong: generate a new temporary token, set `WHATSAPP_ACCESS_TOKEN` again. |
| `100` on the Graph check | `WHATSAPP_PHONE_NUMBER_ID` holds the WhatsApp Business Account ID or a typo. |
| `131030` | The authority's number is not in the test number's allowed list (step 1.3). |
| `131047`, or nothing arrives | Outside the 24 h window: message the test number from the phone, then retry. Set `WHATSAPP_TEMPLATE_NAME` to an approved template with three quick-reply buttons to send outside the window. |
| Meta cannot verify the callback | Tunnel down, server not on 8787, or verify token different from the variable. `python -m hold.whatsapp_check --url …` names which. |
| The tap does nothing | **messages** not subscribed under Webhook fields; or the tunnel URL changed (re-enter it in Meta); or the app secret is not this app's (HOLD answers 401 to unsigned or badly signed events). |
| The demo says the relay is simulated | The four variables are not set in the shell that runs `hold demo`. |

Rehearsed without Meta. A local stand-in for the Graph API (`WHATSAPP_API_BASE`) ran with the real server in `--relay whatsapp` mode and a button reply signed like Meta's. Results:
- the reply resolved the mission (`CLOSED_COMPLETED`);
- a forged signature got 401;
- a repeated delivery was ignored (`ALREADY_PROCESSED`).

The live run against Meta is what remains to be shown.
