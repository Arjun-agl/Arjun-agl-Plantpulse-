"""Record the dashboard's API responses from a running, seeded PlantPulse server.

Used to build the static GitHub Pages demo (see tools/build_static_demo.py).

    pip install playwright
    python main.py            # in another terminal, after seed_demo_data.py + ML training
    python tools/record_demo.py recorded.json [http://127.0.0.1:8000]

Uses Microsoft Edge via Playwright's `channel="msedge"`; change it to "chrome" or install
Playwright's own Chromium if Edge isn't available.
"""
import json, os, sys
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else "http://127.0.0.1:8000"
OUT = sys.argv[1]
DEMO = {"email": "demo@plantpulse.app", "first_name": "Demo", "password": "demo-pass-1234"}
PAGES = ["dashboard", "history", "chat", "pump", "insights", "settings", "devices"]
rec = {}


def key_for(url):
    return url.replace(BASE, "")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    ctx0 = browser.new_context()
    r = ctx0.request.post(BASE + "/api/auth/signup", data=DEMO)
    if not r.ok:
        r = ctx0.request.post(BASE + "/api/auth/login", data={"email": DEMO["email"], "password": DEMO["password"]})
    user = r.json()["user"]
    ctx0.request.post(BASE + "/api/profile", data={
        "user_email": DEMO["email"], "farm_name": "Green Valley Garden", "parcel_name": "Plot A",
        "parcel_area": 0.5, "parcel_unit": "acres", "approximate_plant_count": 40,
        "plants": [{"name": "Tomato bed", "crop_type": "tomato", "growth_stage": "vegetative", "quantity": 24},
                   {"name": "Chilli row", "crop_type": "chilli", "growth_stage": "vegetative", "quantity": 16}]})
    user["profile_complete"] = True
    ctx0.close()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("localStorage.setItem('ppUser', %s);" % json.dumps(json.dumps(user)))
    page = ctx.new_page()

    def on_response(resp):
        u = resp.url
        if not u.startswith(BASE + "/api/"):
            return
        req = resp.request
        k = req.method + " " + key_for(u)
        try:
            rec[k] = {"status": resp.status, "body": resp.text()}
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(BASE)
    page.wait_for_timeout(5000)
    for pid in PAGES:
        page.evaluate("(id)=>{const el=document.querySelector(\"[onclick*=\\\"showPage('\"+id+\"'\\\"]\");showPage(id,el);}", pid)
        page.wait_for_timeout(2500)
    # history + anomaly ranges
    SHOW = "(id)=>{const el=document.querySelector(\"[onclick*=\\\"showPage('\"+id+\"'\\\"]\");showPage(id,el);}"
    page.evaluate(SHOW, "history")
    for rng in ["24h", "7d", "30d", "1y"]:
        page.evaluate("(r)=>{ if (typeof loadHistory==='function') loadHistory(r); }", rng)
        page.wait_for_timeout(1500)
        page.evaluate("(r)=>fetch('/api/anomalies?range='+r)", rng)
        page.wait_for_timeout(500)
    # state-changing calls whose responses the UI displays
    page.evaluate(SHOW, "chat")
    page.wait_for_timeout(800)
    for path, body in [("/api/pump/on", {}), ("/api/pump/off", {}), ("/api/spray/on", {}), ("/api/spray/off", {})]:
        page.evaluate("([p,b])=>fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})", [path, body])
        page.wait_for_timeout(400)
    page.evaluate("""()=>fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:'Do I need to water now?',user_email:'demo@plantpulse.app'})})""")
    page.wait_for_timeout(2500)
    page.evaluate("""()=>fetch('/api/ai/decision/generate?user_email=demo%40plantpulse.app',{method:'POST'})""")
    page.wait_for_timeout(2500)
    browser.close()

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"user": user, "capturedAt": datetime.now().isoformat(timespec="seconds"), "responses": rec}, f, separators=(",", ":"))
print(len(rec), "responses,", round(os.path.getsize(OUT) / 1024), "KB")
for k, v in sorted(rec.items()):
    print("%-70s %s %6d" % (k[:70], v["status"], len(v["body"])))
