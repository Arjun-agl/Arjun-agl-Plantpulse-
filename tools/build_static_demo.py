"""Build the GitHub Pages demo in docs/ from the real dashboard and a recording.

    python tools/record_demo.py recorded.json     # needs the server running + playwright
    python tools/build_static_demo.py recorded.json

The dashboard (index.html) is copied unchanged apart from two <script> tags that load the
recorded data and the fetch shim (docs/demo-shim.js).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

rec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

# Don't publish the recording machine's hostname / LAN address.
info = rec["responses"].get("GET /api/server/info")
if info:
    body = json.loads(info["body"])
    body.update(server_ip="192.168.1.50", hostname="plantpulse-server",
                dashboard_url="http://192.168.1.50:8000",
                ingest_url="http://192.168.1.50:8000/api/ingest")
    info["body"] = json.dumps(body)

data = {"user": rec["user"], "capturedAt": rec["capturedAt"], "responses": rec["responses"]}
(DOCS / "demo-data.js").write_text(
    "window.__PP_DEMO=" + json.dumps(data, separators=(",", ":")) + ";\n", encoding="utf-8")

html = (ROOT / "index.html").read_text(encoding="utf-8")
tags = ('<link rel="icon" href="data:,">\n'
        '<script src="demo-data.js"></script>\n<script src="demo-shim.js"></script>\n')
assert "<head>" in html
(DOCS / "index.html").write_text(html.replace("<head>", "<head>\n" + tags, 1), encoding="utf-8")
print("wrote docs/index.html and docs/demo-data.js")
