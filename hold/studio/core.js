/* HOLD Studio — logique pure, sans interface.
 * Mêmes règles que la bibliothèque Python : forme canonique, empreinte SHA-256, vérification
 * de la chaîne du journal, rejeu des décisions du plancher, validation du profil, aperçu de
 * l'hésitation. Tout s'exécute dans le navigateur, indépendamment de la bibliothèque.
 */
(function (root) {
  "use strict";

  const ENVELOPE_VERSION = "hold.event/v1";
  const GENESIS = "0".repeat(64);
  const LADDER_AUTHORITY = { VERIFY: "agent", CONTACT_SUPPORT: "mandate", ASK_AUTHORITY: "authority", PRESERVE: "none" };
  const EVENT_TYPES = {
    MISSION_OPENED: ["title"], PROFILE_ATTACHED: ["fingerprint", "revision", "profile"],
    ACTION_ADMITTED: ["action", "kind"], ACTION_REFUSED: ["action", "rule", "reason"], ACTION_RESULT: ["action", "status"],
    EFFECT_RESERVED: ["action", "effect_key", "operation_id", "attributes"], EFFECT_OUTCOME: ["effect_key", "operation_id", "status"],
    HOLD_ENTERED: ["effect_key", "operation_id", "scope"], PROPOSAL: ["round", "discerner", "position", "proposal"],
    DECISION: ["round", "position", "proposal", "read_digest", "now_digest", "verdict"], PATH_EXECUTED: ["round", "path", "result"],
    EVIDENCE: ["effect_key", "source", "kind"], DECISION_REQUEST: ["question_id", "kind", "options"],
    AUTHORITY_ANSWER: ["question_id", "answer", "channel"], AUTHORITY_GRANT: ["grant"], RECONCILED: ["effect_key", "receipt"],
    RESUMED: ["effect_key"], PRESERVED: ["effect_key", "reason"], REVOKED: ["by", "channel"],
    RELAY_SENT: ["question_id", "channel", "mode"], RELAY_FAILED: ["channel", "error"], RELAY_REJECTED: ["reason"], MISSION_CLOSED: ["outcome"],
  };

  // -- forme canonique (identique à hold/canonical.py) -------------------------------------
  function normalize(v) {
    if (v === null || v === undefined) return undefined;
    if (typeof v === "number") { if (!Number.isFinite(v)) throw new Error("Nombre non fini"); return v; }
    if (typeof v === "string" || typeof v === "boolean") return v;
    if (Array.isArray(v)) return v.map((x) => { const n = normalize(x); return n === undefined ? null : n; });
    if (typeof v === "object") {
      const out = {};
      for (const k of Object.keys(v)) { const n = normalize(v[k]); if (n !== undefined) out[k] = n; }
      return out;
    }
    throw new Error("Type non canonicalisable");
  }
  function serialize(v) {
    if (v === null) return "null";
    if (Array.isArray(v)) return "[" + v.map(serialize).join(",") + "]";
    if (typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + serialize(v[k])).join(",") + "}";
    return JSON.stringify(v);
  }
  const canonical = (v) => serialize(normalize(v) ?? null);

  async function sha256Hex(text) {
    const subtle = (root.crypto && root.crypto.subtle) || (typeof require === "function" ? require("crypto").webcrypto.subtle : null);
    const buf = await subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  const digest = (v) => sha256Hex(canonical(v));

  function profileBody(p) { const { signature, ...body } = p; return body; }
  const fingerprint = (p) => digest(profileBody(p));

  // -- journal ------------------------------------------------------------------------------
  async function verifyChain(events) {
    let prev = GENESIS;
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      const { hash, ...unsigned } = e;
      if (e.v !== ENVELOPE_VERSION) return { valid: false, count: events.length, brokenAt: e.seq, reason: "unknown envelope version" };
      if (e.seq !== i + 1) return { valid: false, count: events.length, brokenAt: e.seq, reason: "non-contiguous sequence" };
      if (e.prev !== prev) return { valid: false, count: events.length, brokenAt: e.seq, reason: "broken chain" };
      if ((await digest(unsigned)) !== hash) return { valid: false, count: events.length, brokenAt: e.seq, reason: "invalid hash" };
      const missing = (EVENT_TYPES[e.type] || null);
      if (!missing) return { valid: false, count: events.length, brokenAt: e.seq, reason: "unknown type " + e.type };
      if (missing.some((k) => !(k in (e.data || {})))) return { valid: false, count: events.length, brokenAt: e.seq, reason: "missing fields" };
      prev = hash;
    }
    return { valid: true, count: events.length, head: prev };
  }

  function referencePath(paths, ladder) {
    const directed = paths.find((p) => p.open && p.directed);
    if (directed) return directed.name;
    for (const name of ladder) { const p = paths.find((x) => x.name === name); if (p && p.open && p.reaches_tau) return name; }
    return "PRESERVE";
  }

  function admitPath(proposalPath, position, readDigest, nowDigest, onRefused) {
    const ref = position.reference;
    if (readDigest !== nowDigest) return { proposed: proposalPath, admitted: false, applied: null, reference: ref, rule: "I5", reason: "The position changed during deliberation: re-read before acting.", overruled: false };
    const p = position.paths.find((x) => x.name === (proposalPath || ""));
    let reason;
    if (!p) reason = "No usable proposal.";
    else if (!p.open) reason = `Path ${proposalPath} is closed: ${p.reason}.`;
    else return { proposed: proposalPath, admitted: true, applied: proposalPath, reference: ref, rule: "ADMIT", reason: `Path ${proposalPath} is open: ${p.reason}.`, overruled: false };
    const applied = onRefused === "reference" ? ref : "PRESERVE";
    return { proposed: proposalPath, admitted: false, applied, reference: ref, rule: "I4/PATH", reason: `${reason} The floor applies ${applied}.`, overruled: true };
  }

  function decisionReplay(events, profiles) {
    let checked = 0; const mismatches = [];
    for (const e of events) {
      if (e.type !== "DECISION") continue;
      const prof = profiles[e.profile];
      const ladder = prof ? prof.hesitation.ladder : ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "PRESERVE"];
      const d = e.data;
      const ref = referencePath(d.position.paths, ladder);
      const v = admitPath(d.proposal.path, d.position, d.read_digest, d.now_digest, d.on_refused || (prof ? prof.hesitation.on_refused_proposal : "reference"));
      checked++;
      if (ref !== d.position.reference || canonical(v) !== canonical(d.verdict)) mismatches.push({ seq: e.seq, round: d.round, recorded: d.verdict, replayed: v });
    }
    return { checked, matches: mismatches.length === 0, mismatches };
  }

  // -- profil -------------------------------------------------------------------------------
  const IDENT = /^[a-z][a-z0-9_]{0,63}$/;
  function validateProfile(p) {
    const errs = [];
    const add = (loc, msg) => errs.push({ loc, msg });
    if (!p || typeof p !== "object") return [{ loc: "", msg: "Profil vide" }];
    if (p.hold !== "habilitation/v1") add("hold", "Version de profil attendue : habilitation/v1");
    if (!/^[a-z0-9][a-z0-9._-]{1,63}$/.test(p.id || "")) add("id", "Identifiant : minuscules, chiffres, . _ -");
    if (!p.title) add("title", "Titre requis");
    if (!(Number.isInteger(p.revision) && p.revision >= 1)) add("revision", "Révision entière ≥ 1");
    if (p.expires_at && p.issued_at && Date.parse(p.expires_at) <= Date.parse(p.issued_at)) add("expires_at", "Expire avant d'être émis");
    const a = p.authority || {};
    if (!a.name) add("authority.name", "Nom de l'autorité requis");
    if (a.channel === "whatsapp" && !/^[1-9]\d{7,14}$/.test(a.contact || "")) add("authority.contact", "Numéro international, chiffres seuls (ex. 212612345678)");
    const powers = p.powers || [];
    if (!powers.length) add("powers", "Au moins un pouvoir");
    const names = powers.map((x) => x.action);
    powers.forEach((pw, i) => {
      const loc = `powers.${i}`;
      if (!IDENT.test(pw.action || "")) add(loc + ".action", "Nom d'action : minuscules, chiffres, _");
      if (names.indexOf(pw.action) !== i) add(loc + ".action", "Action en double");
      if (pw.kind === "effect") {
        if (!(pw.identity || []).length) add(loc + ".identity", "Un effet doit déclarer ses attributs d'identité");
        const env = pw.envelope;
        if (env && env.max_amount) {
          if (!(env.max_amount.amount > 0)) add(loc + ".envelope.max_amount", "Plafond positif requis");
          if (!/^[A-Z]{3}$/.test(env.max_amount.currency || "")) add(loc + ".envelope.max_amount", "Devise ISO à 3 lettres");
        }
        for (const [k, w] of Object.entries((env && env.windows) || {})) if (w.start > w.end) add(`${loc}.envelope.windows.${k}`, "Fenêtre inversée");
        const rec = pw.reconciliation;
        if (rec) {
          const s = powers.find((x) => x.action === rec.status_action);
          if (!s || s.kind !== "status") add(loc + ".reconciliation", `La réconciliation exige un pouvoir « status » nommé ${rec.status_action}`);
        }
      } else if (pw.envelope || pw.reconciliation || (pw.identity || []).length) add(loc, "Enveloppe, identité et réconciliation sont réservées aux effets");
      for (const d of pw.depends_on || []) { const t = powers.find((x) => x.action === d); if (!t || t.kind !== "effect") add(loc + ".depends_on", `Dépendance inconnue : ${d}`); }
      if (pw.role === "support" && pw.kind !== "contact") add(loc + ".role", "Le rôle support exige un pouvoir de contact");
    });
    if (powers.filter((x) => x.role === "support").length > 1) add("powers", "Un seul pouvoir « support »");
    const h = p.hesitation || {};
    if (!(h.tau_star > 0 && h.tau_star <= 1)) add("hesitation.tau_star", "τ* entre 0 (exclu) et 1");
    if (!h.ladder || h.ladder[h.ladder.length - 1] !== "PRESERVE") add("hesitation.ladder", "PRESERVE doit être le dernier chemin");
    if (h.ladder && new Set(h.ladder).size !== h.ladder.length) add("hesitation.ladder", "Chemin en double");
    return errs;
  }

  /** Ce que fera l'agent si chaque vérification revient « introuvable » et que le support n'aide pas. */
  function simulateHesitation(p, action) {
    const h = p.hesitation; const tau = h.tau_star; const a = p.authority;
    const effect = (p.powers || []).find((x) => x.action === action) || (p.powers || []).find((x) => x.kind === "effect" && x.reconciliation);
    const rec = effect && effect.reconciliation;
    const support = (p.powers || []).find((x) => x.role === "support");
    let notFound = 0, checks = 0, supportUsed = 0; const steps = [];
    for (let i = 0; i < 10; i++) {
      const paths = h.ladder.map((name) => {
        if (name === "VERIFY") { const sigma = rec ? rec.sigma * Math.pow(rec.decay, notFound) : 0; const open = !!rec && checks < rec.max_checks; return { name, open, sigma, reaches_tau: sigma >= tau }; }
        if (name === "CONTACT_SUPPORT") { const sigma = support ? (support.sigma ?? 0.5) : 0; const open = !!support && (support.budget == null || supportUsed < support.budget); return { name, open, sigma, reaches_tau: sigma >= tau }; }
        if (name === "ASK_AUTHORITY") { const open = !!(a && (a.channel === "console" || a.contact)) && (a.max_requests ?? 2) > 0; return { name, open, sigma: a.sigma, reaches_tau: a.sigma >= tau }; }
        return { name, open: true, sigma: 0, reaches_tau: false };
      });
      const ref = referencePath(paths, h.ladder);
      const sigma = (paths.find((x) => x.name === ref) || {}).sigma || 0;
      steps.push({ path: ref, sigma: Math.round(sigma * 100) / 100, authority: LADDER_AUTHORITY[ref] });
      if (ref === "VERIFY") { checks++; notFound++; }
      else if (ref === "CONTACT_SUPPORT") { supportUsed++; notFound++; }
      else break;
    }
    return steps;
  }

  const api = { canonical, digest, sha256Hex, fingerprint, profileBody, verifyChain, decisionReplay, admitPath, referencePath, validateProfile, simulateHesitation, ENVELOPE_VERSION };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HoldCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
