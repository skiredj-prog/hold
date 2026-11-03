"""python -m hold.journal_verify: the journal checks of `hold journal verify`, with a correct exit code."""
import sqlite3

from hold.demo import run
from hold.journal_verify import check


def test_valid_tampered_and_unknown(tmp_path):
    db = str(tmp_path / 'j.db')
    run('lost-response', store=db, color=False, out=lambda *a, **k: None)
    from hold.store import Store
    mission = Store(db).read("SELECT id FROM missions")[0]['id']
    code, lines = check(db, mission)
    assert code == 0, lines
    assert lines[0].startswith('Chain: valid') and 'all recomputed identical' in lines[2]
    con = sqlite3.connect(db)
    seq = con.execute("SELECT seq FROM events WHERE mission=? AND type='EVIDENCE' AND json LIKE '%RCPT-%' ORDER BY seq LIMIT 1", (mission,)).fetchone()[0]
    con.execute("UPDATE events SET json=replace(json, 'RCPT-', 'FAKE-') WHERE mission=? AND seq=?", (mission, seq))
    con.commit(); con.close()
    code, lines = check(db, mission)
    assert code == 1 and 'INVALID' in lines[0]
    assert check(db, 'm_unknown')[0] == 2
