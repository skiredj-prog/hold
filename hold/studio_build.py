"""Assemble HOLD Studio into one page: python -m hold.studio_build

Inlines the pure core (studio/core.js) and the catalog of profile patterns (profiles/*.hold.yaml) into
studio/studio.src.html and writes studio/index.html, which `hold studio` and `hab.serve()` serve at /.
The page also works opened as a file, offline: the catalog is embedded, and validation and journal
verification run in the browser with the same rules as the library.

This module is outside the E1-A6 frozen perimeter (bench.CODE): it reads the frozen files, it never changes them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .profile import load_profile

HERE = Path(__file__).parent
STUDIO = HERE / "studio"


def catalog() -> list[dict]:
    items = []
    for f in sorted((HERE / "profiles").glob("*.hold.yaml")):
        p = load_profile(f)
        items.append({"file": f.name, "id": p.id, "title": p.title, "description": p.description,
                      "profile": p.model_dump(mode="json", exclude_none=True), "fingerprint": p.fingerprint})
    return items


def build(out: Path | None = None) -> Path:
    src = (STUDIO / "studio.src.html").read_text(encoding="utf-8")
    core = (STUDIO / "core.js").read_text(encoding="utf-8")
    for marker in ("/*@CORE@*/", "/*@CATALOG@*/[]"):
        if src.count(marker) != 1:
            raise SystemExit(f"studio.src.html: expected the marker {marker} exactly once")
    data = json.dumps(catalog(), ensure_ascii=False).replace("</", "<\\/")
    page = src.replace("/*@CORE@*/", core.replace("</script", "<\\/script")).replace("/*@CATALOG@*/[]", data)
    out = out or STUDIO / "index.html"
    out.write_bytes(page.encode("utf-8"))  # same bytes on Windows and Linux, so the page's hash can be compared
    return out


if __name__ == "__main__":
    p = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"HOLD Studio written to {p} ({p.stat().st_size // 1024} KB)")
