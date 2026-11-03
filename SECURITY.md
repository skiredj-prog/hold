# Security policy

## Scope

HOLD is a hackathon prototype and research testbed. The primary security boundaries are:

- the signed Meta WhatsApp webhook;
- the local-only HOLD Studio and mission APIs;
- the bearer-token option for intentionally remote Studio access;
- the evidence journal and offline replay verifier.

The synthetic hotel provider, benchmark traces, simulated relay, and exported evidence envelopes are not production services or independent attestations.

## Reporting a vulnerability

Please do not publish credentials, personal data, or an exploitable payload in a public issue. Contact the project owner privately through the contact channel associated with the GitHub account, including:

- a concise description and impact;
- affected commit or file;
- reproduction steps that do not expose secrets or personal data;
- whether the issue affects the local Studio, webhook gateway, relay, journal, or evidence verifier.

If private contact is unavailable, open a minimal public issue stating only that a security report is available; do not include the exploit details until the owner confirms a safe channel.

## Security assumptions and limitations

- Meta webhook authenticity depends on the configured app secret and the `X-Hub-Signature-256` header.
- The public gateway exposes only the webhook path; it must not be replaced with a general-purpose reverse proxy without review.
- Studio mission APIs are intended for loopback access or an explicitly configured bearer token.
- The journal hash chain detects partial alteration but does not prove completeness, external origin, trustworthy timestamps, or independent key custody.
- Never commit WhatsApp tokens, app secrets, verify tokens, tunnel credentials, personal phone numbers, or private evidence exports.

## Safe disclosure status

This policy is part of the hackathon hardening pass. It does not claim that HOLD is production-secure or independently audited.
