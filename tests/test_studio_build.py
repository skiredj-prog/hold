"""HOLD Studio is one page assembled from the frozen core and the profile catalog."""

import hashlib
import json
import re

from hold import studio_build
from hold.profile import load_profile


def test_build_inlines_core_and_catalog(tmp_path):
    page = studio_build.build(tmp_path / "index.html").read_text(encoding="utf-8")
    assert "/*@CORE@*/" not in page and "/*@CATALOG@*/" not in page
    core = (studio_build.STUDIO / "core.js").read_text(encoding="utf-8")
    assert core.replace("</script", "<\\/script") in page  # the frozen core, verbatim
    # each pattern is embedded with the fingerprint the library computes
    for f in sorted((studio_build.HERE / "profiles").glob("*.hold.yaml")):
        p = load_profile(f)
        assert p.fingerprint in page
    # nothing in the data can close the script element early
    m = re.search(r"const CATALOG_EMBEDDED = (.*);\n", page)
    assert m and "</script" not in m.group(1)
    assert len(json.loads(m.group(1).replace("<\\/", "</"))) >= 1


def test_build_is_deterministic(tmp_path):
    a = studio_build.build(tmp_path / "a.html").read_bytes()
    b = studio_build.build(tmp_path / "b.html").read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()
    assert b"\r\n" not in a
