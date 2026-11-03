"""A live mission through the existing governance kernel, not an E1-A6 rerun."""
import hashlib
import hmac
import io
import json
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from experience.core import Experience
from experience.envelope import capture, verify_export
from hold.relay.whatsapp import button_id, simulated_tap


def await_status(x, status, seconds=8):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        s = x.snapshot()
        if s.get('status') == status:
            return s
        if s.get('error'):
            pytest.fail(s['error'])
        time.sleep(0.04)
    pytest.fail(f"mission never reached {status}, got {x.snapshot().get('status')}")


def test_simulated_full_mission_and_evidence(tmp_path):
    x = Experience(db=str(tmp_path / 'sim.db'), wait_seconds=8)
    client = TestClient(x.app)
    assert client.get('/experience').status_code == 200
    assert client.get('/api/experience/state').json()['started'] is False
    assert client.get('/api/experience/envelope').status_code == 409
    started = client.post('/api/experience/start').json()
    assert started['started'] is True
    assert client.post('/api/experience/start').status_code == 409
    st = await_status(x, 'AWAITING_AUTHORITY')
    assert st['synthetic_bookings'] == 1
    assert st['open_request'] is not None
    assert st['chain_valid']
    assert client.get('/api/experience/inspect').json()['findings']['completed_with_evidence'] is False
    early = client.get('/api/experience/envelope')
    assert early.status_code == 200
    with zipfile.ZipFile(io.BytesIO(early.content)) as z:
        assert json.loads(z.read('findings.json'))['completed_with_evidence'] is False
    assert client.post('/api/experience/tap', json={'code': 'NOT_AN_OPTION'}).status_code == 409
    decision = client.post('/api/experience/tap', json={'code':'EXTEND_CHECKS'}).json()
    assert decision['results'][0]['accepted'] is True
    await_status(x, 'CLOSED_COMPLETED')
    final = client.get('/api/experience/state').json()
    assert final['synthetic_bookings'] == 1
    assert sum(1 for q in final['provider_requests'] if q['kind']=='CREATE') == 1
    assert any(e['action']=='send_itinerary' and e['status']=='CONFIRMED' for e in final['effects'])
    assert client.get('/api/experience/inspect').json()['findings']['completed_with_evidence'] is True
    zipped = client.get('/api/experience/envelope').content
    assert verify_export(zipped)['valid']
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        f = json.loads(z.read('findings.json'))
        assert f['completed_with_evidence']
        assert f['checks'] == {'chain_valid':True,'state_matches':True,'decisions_match':True}
        assert json.loads(z.read('synthetic-provider.json'))['create_count'] == 1
        assert len(json.loads(z.read('authority-relay.json'))) >= 2
    # The archived content is checked, but the manifest is not independently signed.
    assert 'independent signature' in verify_export(zipped)['caveat']
    assert client.get('/api/experience/state', headers={'x-forwarded-for':'198.51.100.42'}).status_code == 403
    assert client.post('/api/experience/start', headers={'x-forwarded-for':'198.51.100.42'}).status_code == 403
    assert client.get('/api/experience/envelope', headers={'x-forwarded-for':'198.51.100.42'}).status_code == 403


def test_manifest_detects_mutation(tmp_path):
    x = Experience(db=str(tmp_path/'t.db'))
    x.start()
    data, original = capture(x.mission, x.hotel, channel='simulated', agent='scripted')
    assert verify_export(original)['valid']
    raw=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as z, zipfile.ZipFile(raw,'w') as out:
        for fn in z.namelist():
            content = z.read(fn)
            out.writestr(fn, b'changed' if fn=='mission-journal.json' else content)
    assert not verify_export(raw.getvalue())['valid']


def test_verifier_rejects_suspiciously_compressed_archive():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps({'files_sha256': {'payload.bin': 'x'}}))
        z.writestr('payload.bin', b'0' * 1_000_000)
    result = verify_export(raw.getvalue())
    assert result['valid'] is False and 'compression ratio' in result['reason']


def test_live_path_mock_meta_signed_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv('WHATSAPP_ACCESS_TOKEN', 'MOCK_TOKEN')
    monkeypatch.setenv('WHATSAPP_PHONE_NUMBER_ID', 'MOCK_PHONE')
    monkeypatch.setenv('WHATSAPP_APP_SECRET', 'MOCK_SECRET')
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN', 'MOCK_VERIFY')
    calls=[]
    x=Experience(relay='whatsapp', contact='212600000000', db=str(tmp_path/'cloud.db'), wait_seconds=8)

    def transport(request):
        body=json.loads(request.content)
        calls.append(body)
        return httpx.Response(200,json={'messages':[{'id':f'wamid.offline.{len(calls)}'}]})

    x.hab.relay.client=httpx.Client(transport=httpx.MockTransport(transport))
    client=TestClient(x.app)
    assert client.get('/api/whatsapp/webhook',params={'hub.mode':'subscribe','hub.verify_token':'MOCK_VERIFY','hub.challenge':'1234'}).text=='1234'
    assert client.post('/api/experience/tap', json={'code':'EXTEND_CHECKS'}).status_code == 409
    client.post('/api/experience/start')
    st=await_status(x,'AWAITING_AUTHORITY')
    assert len(calls)==1 and calls[0]['type']=='interactive'
    q=st['open_request']
    payload=simulated_tap('212600000000',button_id(st['mission_id'],q['question_id'],'EXTEND_CHECKS'),'Allow 1 more check')
    payload['entry'][0]['changes'][0]['value']['metadata']={'phone_number_id':'MOCK_PHONE'}
    raw=json.dumps(payload).encode()
    bad=client.post('/api/whatsapp/webhook',content=raw,headers={'x-hub-signature-256':'sha256='+'0'*64})
    assert bad.status_code==401
    sig='sha256='+hmac.new(b'MOCK_SECRET',raw,hashlib.sha256).hexdigest()
    accepted=client.post('/api/whatsapp/webhook',content=raw,headers={'x-hub-signature-256':sig})
    assert accepted.status_code==200 and accepted.json()['results'][0]['accepted']
    duplicate=client.post('/api/whatsapp/webhook',content=raw,headers={'x-hub-signature-256':sig})
    assert duplicate.json()['results'][0].get('duplicate')
    await_status(x,'CLOSED_COMPLETED')
    assert x.hotel.created == 1
    assert x.mission.verify().valid
    assert len(calls) >= 2 # the decision request and its acknowledgment
    assert verify_export(client.get('/api/experience/envelope').content)['valid']


def test_real_mode_fails_closed_without_meta_configuration(tmp_path,monkeypatch):
    for n in ('WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_APP_SECRET','WHATSAPP_VERIFY_TOKEN'):
        monkeypatch.delenv(n,raising=False)
    with pytest.raises(ValueError, match='requires'):
        Experience(relay='whatsapp',contact='212600000000',db=str(tmp_path/'nowhere.db'))


def test_public_gateway_refuses_experience_controls():
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from experience.gateway import handler_for

    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for())
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ('/experience', '/api/experience/state', '/api/experience/envelope',
                     '/api/missions', '/api/health'):
            with pytest.raises(HTTPError) as failure:
                urlopen(f'http://127.0.0.1:{server.server_port}{path}', timeout=2)
            assert failure.value.code == 404, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_mission_story_is_backed_by_events_and_raw_zip(tmp_path):
    x = Experience(db=str(tmp_path / "story.db"), wait_seconds=8)
    c = TestClient(x.app)
    assert c.get("/api/experience/evidence/manifest.json").status_code == 409
    assert c.get("/api/experience/evidence/evil.json").status_code == 404
    assert c.get("/api/experience/evidence/findings.json", headers={"x-forwarded-for": "203.0.113.10"}).status_code == 403
    html = c.get("/experience").text
    assert "FOR THE AI AGENT ON THE GROUND" in html
    assert "META · OPEN-WEIGHT MODEL" in html
    assert "The current default demonstration uses a scripted agent" in html
    assert "function milestones(s)" in html
    assert "The provider did not answer" in html
    c.post("/api/experience/start")
    st = await_status(x, "AWAITING_AUTHORITY")
    assert any(q['kind']=='CREATE' and q['response']=='LOST_RESPONSE' for q in st['provider_requests'])
    assert any(e['type']=='HOLD_ENTERED' for e in st['events'])
    assert any(e['type']=='DECISION_REQUEST' for e in st['events'])
    r = c.get("/api/experience/evidence/synthetic-provider.json").json()
    assert r['matches']
    assert r['data']['create_count'] == 1
    assert r['expected_sha256'] == r['sha256']
    m = c.get("/api/experience/evidence/manifest.json").json()
    assert m['matches'] is None and m['expected_sha256'] is None
    assert m['data']['files_sha256']['synthetic-provider.json'] == r['sha256']
    assert c.get("/api/experience/evidence/manifest.json",headers={"forwarded": "for=192.0.2.2"}).status_code == 403
    x.tap("EXTEND_CHECKS")
    final = await_status(x, "CLOSED_COMPLETED")
    assert any(e['type']=='RECONCILED' for e in final['events'])
    assert sum(q['kind']=='CREATE' for q in final['provider_requests'])==1
    journal = c.get("/api/experience/evidence/mission-journal.json").json()
    assert journal['matches'] and journal['data']


def test_jury_guide_explains_scenario_before_start_and_keeps_codes_optional():
    from pathlib import Path
    ui = (Path(__file__).resolve().parents[1] / 'experience' / 'index.html').read_text(encoding='utf-8')
    assert '01 · USE CASE' in ui and '02 · SCENARIO' in ui
    assert "03 · WHAT YOU'LL SEE" in ui and '04 · WHY IT MATTERS' in ui
    assert ui.index('Did the hotel book it') < ui.index('id="start"')
    assert 'Start the guided booking' in ui
    assert ui.index('id="reply"') < ui.index('id="storySteps"')
    assert 'Yes — check the existing booking once' in ui
    assert 'Stop without claiming success' in ui
    assert 'Show the technical event log (optional)' in ui
    assert 'id="guideActionTitle"' in ui and 'function guide(s)' in ui
    assert 'Meta Llama 3.2' in ui and 'Meta WhatsApp Cloud API' in ui
    assert 'scripted agent and simulated authority replies' in ui


def _rewrite(zipped, name, mutate):
    """Rewrite one file AND its manifest entry, as someone editing the archive would."""
    src = zipfile.ZipFile(io.BytesIO(zipped))
    files = {n: src.read(n) for n in src.namelist()}
    files[name] = mutate(files[name])
    manifest = json.loads(files['manifest.json'])
    manifest['files_sha256'][name] = hashlib.sha256(files[name]).hexdigest()
    files['manifest.json'] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, data in files.items():
            z.writestr(n, data)
    return out.getvalue()


def test_offline_verifier_rechecks_the_journal_itself(tmp_path):
    from experience.verify import verify_archive
    x = Experience(db=str(tmp_path / 'v.db'), wait_seconds=8)
    x.start()
    await_status(x, 'AWAITING_AUTHORITY')
    x.tap('EXTEND_CHECKS')
    await_status(x, 'CLOSED_COMPLETED')
    _, zipped = capture(x.mission, x.hotel, channel='simulated', agent='scripted')
    ok = verify_archive(zipped)
    assert ok['valid'] and ok['journal']['chain']['valid'] and ok['journal']['decisions']['checked'] >= 4
    assert ok['journal']['state_vs_store']['matches']

    def forge_receipt(raw):  # claim a different receipt in the journal, and fix the manifest
        j = json.loads(raw)
        for e in j['events']:
            if e['type'] == 'EVIDENCE' and 'RCPT-' in json.dumps(e['data']):
                e['data'] = json.loads(json.dumps(e['data']).replace('RCPT-', 'FORGED-'))
                break
        else:
            raise AssertionError('no receipt evidence in the journal')
        return json.dumps(j).encode()
    forged = _rewrite(zipped, 'mission-journal.json', forge_receipt)
    r = verify_archive(forged)
    assert r['manifest']['valid'] is True          # the manifest alone is fooled
    assert r['valid'] is False and r['journal']['chain']['valid'] is False   # the journal re-check is not

    def forge_store(raw):  # keep the events, change the store snapshot exported beside them
        j = json.loads(raw)
        j['store']['status'] = 'CLOSED_UNCONFIRMED'
        return json.dumps(j).encode()
    r2 = verify_archive(_rewrite(zipped, 'mission-journal.json', forge_store))
    assert r2['valid'] is False and r2['journal']['state_vs_store']['differences'] == ['status']
