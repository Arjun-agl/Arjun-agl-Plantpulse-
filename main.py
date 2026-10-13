
import sqlite3, json, os, re, time, math, uuid, hashlib, hmac, secrets
from datetime import datetime, timedelta
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from dotenv import load_dotenv
import requests as req_lib
import io, csv

# Anchor all file paths to this script's folder so the app works
# no matter where the project folder is placed or launched from
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DB_PATH = os.path.join(BASE_DIR, "plantpulse.db")

CROP_DB = {
    "strawberry":  {"name":"🍓 Strawberry","dry":55,"wet":85,"temp_min":18,"temp_max":24,"heat":28,"hum_min":40,"hum_max":65,"note":"Fungal risk >75% humidity"},
    "tomato":      {"name":"🍅 Tomato","dry":45,"wet":85,"temp_min":18,"temp_max":27,"heat":35,"hum_min":65,"hum_max":85,"note":"Inconsistent watering → blossom end rot"},
    "chilli":      {"name":"🌶 Chilli","dry":35,"wet":78,"temp_min":20,"temp_max":32,"heat":38,"hum_min":60,"hum_max":80,"note":"Good drought tolerance"},
    "capsicum":    {"name":"🫑 Capsicum","dry":40,"wet":80,"temp_min":20,"temp_max":30,"heat":35,"hum_min":70,"hum_max":80,"note":"Flower drop if dry during fruiting"},
    "cucumber":    {"name":"🥒 Cucumber","dry":50,"wet":90,"temp_min":21,"temp_max":30,"heat":35,"hum_min":70,"hum_max":90,"note":"Mildew risk at high humidity"},
    "spinach":     {"name":"🥬 Spinach","dry":45,"wet":85,"temp_min":10,"temp_max":21,"heat":27,"hum_min":60,"hum_max":80,"note":"Bolts in heat"},
    "lettuce":     {"name":"🥗 Lettuce","dry":45,"wet":88,"temp_min":10,"temp_max":20,"heat":25,"hum_min":60,"hum_max":80,"note":"Shallow roots"},
    "mint":        {"name":"🌿 Mint","dry":40,"wet":82,"temp_min":18,"temp_max":28,"heat":33,"hum_min":50,"hum_max":75,"note":"Tolerates more moisture than basil"},
    "basil":       {"name":"🌿 Basil","dry":38,"wet":78,"temp_min":18,"temp_max":28,"heat":33,"hum_min":50,"hum_max":70,"note":"Cold-sensitive"},
    "coriander":   {"name":"🌿 Coriander","dry":38,"wet":78,"temp_min":17,"temp_max":27,"heat":32,"hum_min":50,"hum_max":70,"note":"Bolts in heat"},
    "cactus":      {"name":"🌵 Cactus","dry":5,"wet":50,"temp_min":18,"temp_max":38,"heat":42,"hum_min":10,"hum_max":40,"note":"Overwatering = death"},
    "fern":        {"name":"🌿 Fern","dry":50,"wet":92,"temp_min":15,"temp_max":24,"heat":28,"hum_min":60,"hum_max":90,"note":"Never let dry"},
    "carrot":      {"name":"🥕 Carrot","dry":40,"wet":80,"temp_min":15,"temp_max":22,"heat":30,"hum_min":60,"hum_max":80,"note":"Overwatering causes rot"},
    "onion":       {"name":"🧅 Onion","dry":40,"wet":82,"temp_min":13,"temp_max":24,"heat":30,"hum_min":55,"hum_max":75,"note":"Reduce water near maturity"},
    "broccoli":    {"name":"🥦 Broccoli","dry":45,"wet":85,"temp_min":14,"temp_max":20,"heat":27,"hum_min":60,"hum_max":80,"note":"Bolts in heat"},
    "eggplant":    {"name":"🍆 Eggplant","dry":40,"wet":82,"temp_min":22,"temp_max":32,"heat":38,"hum_min":65,"hum_max":80,"note":"Cold-sensitive"},
    "custom":      {"name":"⚙️ Custom","dry":40,"wet":80,"temp_min":18,"temp_max":30,"heat":35,"hum_min":50,"hum_max":80,"note":"User-defined thresholds"},
}

GROWTH_STAGES = {
    "seedling":   {"moisture_min":55,"moisture_max":70,"temp_max":22,"hum_max":65,"note":"Gentle watering; avoid waterlogging"},
    "vegetative": {"moisture_min":60,"moisture_max":75,"temp_max":24,"hum_max":65,"note":"Normal growth"},
    "flowering":  {"moisture_min":60,"moisture_max":75,"temp_max":22,"hum_max":50,"note":"Lower humidity aids pollination"},
    "fruiting":   {"moisture_min":65,"moisture_max":80,"temp_max":24,"hum_max":70,"note":"Consistent moisture critical"},
}

# ─────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        raw INTEGER, moisture_pct REAL, scale REAL,
        temp REAL, humidity REAL, light REAL,
        source TEXT DEFAULT 'wifi',
        note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS watering_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        method TEXT DEFAULT 'manual',
        moisture_before REAL, moisture_after REAL,
        note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        type TEXT, severity TEXT, message TEXT, value REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_threads (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts TEXT NOT NULL,
        FOREIGN KEY(thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_updated ON chat_threads(updated_ts DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_ts ON chat_messages(thread_id, id)")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        first_name TEXT NOT NULL,
        password_hash TEXT,
        created_ts TEXT NOT NULL,
        last_login_ts TEXT
    )""")
    # migrations for DBs created by older versions
    def ensure_column(table, col, decl):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    ensure_column("users", "password_hash", "TEXT")
    ensure_column("chat_threads", "user_email", "TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS user_farm_profiles (
        user_email TEXT PRIMARY KEY,
        farm_name TEXT NOT NULL,
        parcel_name TEXT NOT NULL,
        parcel_area REAL NOT NULL,
        parcel_unit TEXT NOT NULL,
        approximate_plant_count INTEGER NOT NULL,
        plants_json TEXT NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL
    )""")
    # soil nutrient sensors (NPK / pH / EC) — mocked until hardware arrives
    ensure_column("readings", "n_val", "REAL")
    ensure_column("readings", "p_val", "REAL")
    ensure_column("readings", "k_val", "REAL")
    ensure_column("readings", "ph", "REAL")
    ensure_column("readings", "ec", "REAL")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_ts TEXT NOT NULL,
        day_key TEXT NOT NULL,
        features_json TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        provider TEXT
    )""")
    ensure_column("ai_decisions", "user_email", "TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        verdict TEXT NOT NULL,
        note TEXT,
        features_json TEXT,
        FOREIGN KEY(decision_id) REFERENCES ai_decisions(id) ON DELETE CASCADE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS watering_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_ts TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        ai_summary TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pump_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts TEXT, stop_ts TEXT,
        mode TEXT, stop_reason TEXT,
        moisture_before REAL, moisture_after REAL,
        duration_sec INTEGER
    )""")
    conn.commit()
    # defaults
    defaults = {
        "plant_name": "My Strawberry",
        "crop_type": "strawberry",
        "growth_stage": "vegetative",
        "air_value": "900",
        "water_value": "350",
        "ntfy_topic": "",
        "dark_mode": "true",
        "pump_mode": "manual",
        "pump_enabled": "false",
        "pump_max_runtime": "300",
        "pump_flow_rate": "100",
        "pump_watering_window_start": "06:00",
        "pump_watering_window_end": "08:00",
        "latitude": "19.0760",
        "longitude": "72.8777",
        "plant_outdoor": "true",
        "pump_auto_on_below": "55",
        "pump_auto_off_at": "75",
        "pump_rule_temp_max": "",
        "pump_rule_humidity_max": "",
        "pump_rule_n_min": "",
        "pump_rule_p_min": "",
        "pump_rule_k_min": "",
        "pump_rule_ph_min": "",
        "pump_rule_ph_max": "",
        "pump_rule_ec_max": "",
        "pump_rule_rain_max": "",
        "pump_action_type": "water",
        "pump_fertilizer_method": "fertigation",
        "pump_fertilizer_concentration": "",
        "pump_fertilizer_tank_liters": "",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
    for legacy_key in ("gemini_key", "groq_key", "groq_model"):
        c.execute("DELETE FROM settings WHERE key=?", (legacy_key,))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def get_setting(key, default=""):
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return r["value"] if r else default

def set_setting(key, value):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    conn.commit(); conn.close()

def moisture_pct(raw, air, water):
    pct = (air - raw) / (air - water) * 100
    return max(0, min(100, round(pct, 1)))

def get_plant_status(m, t, h, crop):
    c = CROP_DB.get(crop, CROP_DB["strawberry"])
    if m is None: return "Unknown", "gray"
    if m < c["dry"]: return "Needs Water 💧", "#ff5d73"
    if m > c["wet"]: return "Overwatered 🌊", "#00d8ff"
    if t and t > c["heat"]: return "Too Hot 🌡️", "#ff8a2a"
    if h and h < c["hum_min"]: return "Low Humidity 🌬️", "#ff8a2a"
    if h and crop == "strawberry" and h > 75: return "Fungal Risk ⚠️", "#ff8a2a"
    return "Healthy 🌱", "#1cff95"

def detect_anomalies(readings):
    anomalies = []
    for i in range(1, len(readings)):
        prev = readings[i-1]; cur = readings[i]
        if cur["moisture_pct"] and prev["moisture_pct"]:
            diff = cur["moisture_pct"] - prev["moisture_pct"]
            if diff <= -15:
                anomalies.append({"type":"rapid_drop","severity":"warning",
                    "ts":cur["ts"],"message":f"Moisture dropped {abs(diff):.1f}% in one interval","value":diff})
            if diff >= 15:
                anomalies.append({"type":"watering_detected","severity":"info",
                    "ts":cur["ts"],"message":f"Possible watering detected — moisture rose {diff:.1f}%","value":diff})
        if cur["temp"] and prev["temp"] and (cur["temp"]-prev["temp"]) >= 5:
            anomalies.append({"type":"temp_spike","severity":"warning",
                "ts":cur["ts"],"message":f"Temperature spiked by {cur['temp']-prev['temp']:.1f}°C","value":cur["temp"]})
    return anomalies

def check_sensor_health(readings, window=10):
    if len(readings) < window: return None
    recent = [r["moisture_pct"] for r in readings[-window:] if r["moisture_pct"] is not None]
    if not recent: return None
    if len(set(recent)) == 1: return "Sensor may be stuck — same value for last readings"
    if max(recent)-min(recent) > 60: return "Sensor readings vary wildly — check wiring"
    return None

def send_ntfy(topic, title, message, priority="default"):
    if not topic: return
    try:
        req_lib.post(f"https://ntfy.sh/{topic}", data=message.encode(),
                     headers={"Title": title, "Priority": priority}, timeout=5)
    except: pass

def drying_rate(readings):
    """Estimate average moisture drop per hour from last 6h of readings."""
    if len(readings) < 2: return None
    drops = []
    for i in range(1, len(readings)):
        prev = readings[i-1]; cur = readings[i]
        if cur["moisture_pct"] and prev["moisture_pct"]:
            diff = prev["moisture_pct"] - cur["moisture_pct"]
            try:
                mins = (datetime.fromisoformat(cur["ts"]) - datetime.fromisoformat(prev["ts"])).total_seconds()/60
                if mins > 0 and diff > 0:
                    drops.append(diff / (mins/60))
            except: pass
    return round(sum(drops)/len(drops), 2) if drops else None

def now_ts():
    return datetime.now().isoformat(timespec="seconds")

def normalize_chat_title(text, fallback="New chat"):
    clean = " ".join((text or "").split()).strip()
    if not clean:
        return fallback
    if len(clean) <= 48:
        return clean
    return clean[:45].rstrip() + "..."

def get_ai_api_key():
    return os.getenv("GROQ_API_KEY", "").strip()

def get_ai_model():
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"

def get_gemini_api_key():
    for name in ("GEMINI_API_KEY", "GEMINI_API", "GEMINI_KEY", "GOOGLE_API_KEY"):
        v = os.getenv(name, "").strip()
        if v:
            return v
    return ""

def get_gemini_model():
    return os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip() or "gemini-flash-latest"

# Hard cap on AI reply length (tokens). The style prompt keeps normal answers much
# shorter; this is the backstop so long essays are never generated or paid for.
try:
    AI_MAX_TOKENS = max(100, int(os.getenv("AI_MAX_TOKENS", "450")))
except ValueError:
    AI_MAX_TOKENS = 450

def build_plant_context(rows, crop, plant, stage):
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    last_m = rows[0]["moisture_pct"] if rows else None
    last_t = rows[0]["temp"] if rows else None
    last_h = rows[0]["humidity"] if rows else None
    dr = drying_rate(rows[::-1])
    context = f"""
Plant: {plant} | Crop: {crop_info['name']} | Stage: {stage}
Current moisture: {last_m}% | Temp: {last_t}°C | Humidity: {last_h}%
Dry threshold: {crop_info['dry']}% | Wet threshold: {crop_info['wet']}%
Ideal temp: {crop_info['temp_min']}–{crop_info['temp_max']}°C
Drying rate: {dr} %/hr | Last 24h readings: {len(rows)} samples
Crop note: {crop_info['note']}
Stage note: {GROWTH_STAGES.get(stage,{}).get('note','')}
""".strip()
    return {
        "crop_info": crop_info,
        "last_m": last_m,
        "last_t": last_t,
        "last_h": last_h,
        "drying_rate": dr,
        "context": context,
    }

def build_chat_system_prompt(context_text):
    return (
        "You are PlantPulse AI, a helpful plant care assistant.\n"
        "Use the provided plant context and the conversation history to answer clearly and practically.\n"
        "If sensor data is missing, say so in one short sentence instead of guessing.\n"
        "Never invent sensor values or claim a health issue without evidence.\n"
        "RESPONSE STYLE (strict):\n"
        "- Be SHORT and direct. Answer the exact question first, in 1-3 sentences.\n"
        "- Maximum 5 short bullet points when a list truly helps. No long tables, no essays.\n"
        "- Do not add background theory, checklists, or extra tips unless the user asks.\n"
        "- Only give a longer, detailed answer when the user explicitly asks for details, "
        "a plan, or an explanation.\n"
        "- End with at most ONE short follow-up offer like 'Want the full plan?' when there is "
        "genuinely more to share.\n\n"
        f"{context_text}"
    )

def build_offline_chat_reply(query, plant, crop, crop_info, last_m, last_t, last_h, dr):
    q = query.lower()
    if "water" in q and ("should" in q or "need" in q or "now" in q):
        if last_m is None:
            resp = "No sensor data yet. Please connect your ESP8266."
        elif last_m < crop_info["dry"]:
            resp = f"Yes! Moisture is {last_m}% which is below the dry threshold of {crop_info['dry']}% for {crop}. Water now."
        elif last_m > crop_info["wet"]:
            resp = f"No. Moisture is {last_m}% - above the wet limit of {crop_info['wet']}%. The soil is already very moist."
        else:
            resp = f"Not yet. Moisture is {last_m}% which is in the healthy range ({crop_info['dry']}-{crop_info['wet']}%)."
        if dr:
            hrs = round((last_m - crop_info["dry"]) / dr, 1) if dr > 0 and last_m else "N/A"
            resp += f" At the current drying rate of {dr}%/hr, watering may be needed in ~{hrs} hours."
    elif "temp" in q or "hot" in q:
        if last_t:
            resp = f"Current temperature is {last_t}°C. Ideal for {crop} is {crop_info['temp_min']}-{crop_info['temp_max']}°C. Heat alert above {crop_info['heat']}°C."
        else:
            resp = "No temperature data available yet."
    elif "humid" in q:
        if last_h:
            resp = f"Current humidity is {last_h}%. Ideal for {crop} is {crop_info['hum_min']}-{crop_info['hum_max']}%."
        else:
            resp = "No humidity data available yet."
    elif "fungal" in q or "disease" in q or "botrytis" in q or "pest" in q:
        if crop == "strawberry" and last_h and last_h > 75:
            resp = f"⚠️ Botrytis (grey mould) risk is elevated - humidity is {last_h}%, above the 75% safe limit. Improve airflow and avoid wetting the leaves or flowers."
        else:
            resp = f"No disease risk detected at current humidity. Monitor {crop_info['note']}."
    elif "summary" in q or "status" in q:
        status, _ = get_plant_status(last_m, last_t, last_h, crop)
        resp = f"Plant: {plant} | Status: {status}\nMoisture: {last_m}%, Temp: {last_t}°C, Humidity: {last_h}%\n{crop_info['note']}"
    else:
        resp = f"I am PlantPulse AI (offline mode). Current: moisture={last_m}%, temp={last_t}°C, humidity={last_h}%."
    return resp

def ensure_chat_thread(conn, thread_id=None, title="New chat", user_email=None):
    c = conn.cursor()
    if thread_id:
        thread = c.execute("SELECT id FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
        if thread:
            return thread["id"]
    created_id = thread_id or str(uuid.uuid4())
    ts = now_ts()
    c.execute(
        "INSERT OR IGNORE INTO chat_threads (id, title, created_ts, updated_ts, user_email) VALUES (?,?,?,?,?)",
        (created_id, title, ts, ts, user_email),
    )
    return created_id

def touch_chat_thread(conn, thread_id, title=None):
    if title is None:
        conn.execute("UPDATE chat_threads SET updated_ts=? WHERE id=?", (now_ts(), thread_id))
    else:
        conn.execute("UPDATE chat_threads SET title=?, updated_ts=? WHERE id=?", (title, now_ts(), thread_id))

def get_chat_threads(limit=30, user_email=None):
    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        """
        SELECT t.id, t.title, t.created_ts, t.updated_ts,
               COUNT(m.id) AS message_count,
               MAX(m.ts) AS last_message_ts,
               COALESCE((SELECT content FROM chat_messages x WHERE x.thread_id = t.id ORDER BY x.id DESC LIMIT 1), '') AS last_message
        FROM chat_threads t
        LEFT JOIN chat_messages m ON m.thread_id = t.id
        WHERE (? IS NULL AND t.user_email IS NULL) OR t.user_email = ?
        GROUP BY t.id
        ORDER BY t.updated_ts DESC
        LIMIT ?
        """,
        (user_email, user_email, limit),
    ).fetchall()
    conn.close()
    threads = []
    for r in rows:
        preview = " ".join((r["last_message"] or "").split()).strip()
        if len(preview) > 90:
            preview = preview[:87].rstrip() + "..."
        threads.append({
            "id": r["id"],
            "title": r["title"],
            "created_ts": r["created_ts"],
            "updated_ts": r["updated_ts"],
            "message_count": r["message_count"],
            "last_message_ts": r["last_message_ts"],
            "preview": preview,
        })
    return threads

def get_chat_thread_payload(thread_id, limit=200):
    conn = get_db(); c = conn.cursor()
    thread = c.execute("SELECT * FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
    if not thread:
        conn.close()
        return None
    messages = c.execute(
        "SELECT role, content, ts FROM chat_messages WHERE thread_id=? ORDER BY id ASC LIMIT ?",
        (thread_id, limit),
    ).fetchall()
    conn.close()
    return {
        "thread": dict(thread),
        "messages": [dict(m) for m in messages],
    }

def save_chat_message(conn, thread_id, role, content):
    conn.execute(
        "INSERT INTO chat_messages (thread_id, role, content, ts) VALUES (?,?,?,?)",
        (thread_id, role, content, now_ts()),
    )

def build_groq_messages(thread_history, system_prompt, user_message):
    messages = [{"role": "system", "content": system_prompt}]
    for entry in thread_history:
        if entry["role"] in {"user", "assistant"}:
            messages.append({"role": entry["role"], "content": entry["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages

def call_groq_chat(messages):
    api_key = get_ai_api_key()
    if not api_key:
        raise RuntimeError("Groq is not configured")
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": get_ai_model(),
        "messages": messages,
        "temperature": 0.4,
        # reasoning models burn hidden thinking tokens from the same budget,
        # so keep effort low and leave headroom above the visible-answer cap
        "reasoning_effort": "low",
        "max_tokens": AI_MAX_TOKENS + 1000,
    }
    r = req_lib.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

def call_gemini_chat(messages):
    # Gemini's OpenAI-compatible endpoint accepts the same message format as Groq
    api_key = get_gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini is not configured")
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    payload = {
        "model": get_gemini_model(),
        "messages": messages,
        "temperature": 0.4,
        "reasoning_effort": "low",
        "max_tokens": AI_MAX_TOKENS + 1000,
    }
    r = req_lib.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
app = FastAPI(title="PlantPulse")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
init_db()

# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────
class SensorReading(BaseModel):
    raw: Optional[int] = None
    moisture_pct: Optional[float] = None
    scale: Optional[float] = None
    temp: Optional[float] = None
    humidity: Optional[float] = None
    light: Optional[float] = None
    n_val: Optional[float] = None
    p_val: Optional[float] = None
    k_val: Optional[float] = None
    ph: Optional[float] = None
    ec: Optional[float] = None
    source: Optional[str] = "wifi"
    rssi: Optional[int] = None
    device_id: Optional[str] = None
    note: Optional[str] = None

class WateringEntry(BaseModel):
    note: Optional[str] = None

class ChatMessage(BaseModel):
    message: str
    thread_id: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None

class UserSignup(BaseModel):
    email: str
    first_name: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class PasswordChange(BaseModel):
    email: str
    old_password: str
    new_password: str

class FarmPlant(BaseModel):
    name: str
    crop_type: str
    growth_stage: str = "vegetative"
    quantity: int = 1

class FarmProfileUpdate(BaseModel):
    user_email: str
    farm_name: str
    parcel_name: str
    parcel_area: float
    parcel_unit: str
    approximate_plant_count: int
    plants: list[FarmPlant]

class ChatThreadCreate(BaseModel):
    title: Optional[str] = None
    user_email: Optional[str] = None

class SettingsUpdate(BaseModel):
    plant_name: Optional[str] = None
    crop_type: Optional[str] = None
    growth_stage: Optional[str] = None
    air_value: Optional[str] = None
    water_value: Optional[str] = None
    ntfy_topic: Optional[str] = None
    dark_mode: Optional[str] = None
    pump_mode: Optional[str] = None
    pump_enabled: Optional[str] = None
    pump_max_runtime: Optional[str] = None
    pump_flow_rate: Optional[str] = None
    pump_watering_window_start: Optional[str] = None
    pump_watering_window_end: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    plant_outdoor: Optional[str] = None
    pump_auto_on_below: Optional[str] = None
    pump_auto_off_at: Optional[str] = None
    pump_rule_temp_max: Optional[str] = None
    pump_rule_humidity_max: Optional[str] = None
    pump_rule_n_min: Optional[str] = None
    pump_rule_p_min: Optional[str] = None
    pump_rule_k_min: Optional[str] = None
    pump_rule_ph_min: Optional[str] = None
    pump_rule_ph_max: Optional[str] = None
    pump_rule_ec_max: Optional[str] = None
    pump_rule_rain_max: Optional[str] = None
    pump_action_type: Optional[str] = None
    pump_fertilizer_method: Optional[str] = None
    pump_fertilizer_concentration: Optional[str] = None
    pump_fertilizer_tank_liters: Optional[str] = None

class PumpModeUpdate(BaseModel):
    mode: str  # manual or auto

# ─────────────────────────────────────────────
# SMART WATERING ENGINE
# weather (Open-Meteo, free/no key) + live sensors + crop profile
# ─────────────────────────────────────────────
weather_cache = {"ts": 0.0, "key": None, "data": None}

def get_weather_forecast(lat, lon):
    now = time.time()
    key = f"{lat},{lon}"
    if weather_cache["data"] and weather_cache["key"] == key and now - weather_cache["ts"] < 1800:
        return weather_cache["data"]
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,"
        "precipitation,et0_fao_evapotranspiration"
        "&forecast_days=3&timezone=auto"
    )
    r = req_lib.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    weather_cache.update(ts=now, key=key, data=data)
    return data

def summarize_weather_24h(w):
    hours = w["hourly"]
    times = hours["time"]
    now_hour = datetime.now().strftime("%Y-%m-%dT%H:00")
    try:
        idx = times.index(now_hour)
    except ValueError:
        idx = 0
    rng = slice(idx, idx + 24)
    temps = [x for x in hours["temperature_2m"][rng] if x is not None]
    probs = [x for x in hours["precipitation_probability"][rng] if x is not None]
    rain = [x for x in hours["precipitation"][rng] if x is not None]
    et0 = [x for x in hours["et0_fao_evapotranspiration"][rng] if x is not None]
    rain_in_hours = None
    for i, (p, mm) in enumerate(zip(hours["precipitation_probability"][rng], hours["precipitation"][rng])):
        if (p or 0) >= 60 and (mm or 0) >= 0.5:
            rain_in_hours = i
            break
    return {
        "max_temp": max(temps) if temps else None,
        "max_rain_prob": max(probs) if probs else None,
        "total_rain_mm": round(sum(rain), 1) if rain else 0,
        "et0_total_mm": round(sum(et0), 2) if et0 else None,
        "rain_in_hours": rain_in_hours,
    }

def compute_smart_plan(crop_override=None, plant_override=None):
    conn = get_db(); c = conn.cursor()
    latest = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    rows_12h = [dict(r) for r in c.execute(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
        ((datetime.now() - timedelta(hours=12)).isoformat(),)).fetchall()]
    conn.close()
    crop = crop_override or get_setting("crop_type", "strawberry")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    plant = plant_override or get_setting("plant_name", "My Plant")
    outdoor = get_setting("plant_outdoor", "true") == "true"
    m = latest["moisture_pct"] if latest else None
    t = latest["temp"] if latest else None
    dr = drying_rate(rows_12h)

    wsum = None
    try:
        wsum = summarize_weather_24h(get_weather_forecast(
            get_setting("latitude", "19.0760"), get_setting("longitude", "72.8777")))
    except Exception as e:
        print(f"[PlantPulse] Weather fetch failed: {e}")

    reasons = []
    action = "monitor"
    eta_hours = None

    if m is None:
        action = "no_data"
        reasons.append("No sensor data yet — connect your ESP8266 to unlock smart planning")
    else:
        # forecast-adjusted drying rate: hotter air ahead pulls water out faster
        adj_dr = dr
        if dr and wsum and wsum["max_temp"] is not None and t and wsum["max_temp"] > t + 2:
            adj_dr = round(dr * 1.25, 2)
            reasons.append(f"Hotter hours ahead (up to {wsum['max_temp']}°C) — soil will dry ~25% faster than now")
        rain_soon = bool(outdoor and wsum and wsum["rain_in_hours"] is not None and wsum["total_rain_mm"] >= 2)

        if m < crop_info["dry"]:
            if rain_soon and wsum["rain_in_hours"] <= 2:
                action = "hold_rain"
                reasons.append(f"Soil is dry ({m}%) but rain is expected within {wsum['rain_in_hours']}h "
                               f"({wsum['total_rain_mm']}mm forecast) — hold off briefly")
            else:
                action = "water_now"
                reasons.append(f"Moisture {m}% is below the {crop_info['dry']}% dry threshold for {crop}")
        elif m > crop_info["wet"]:
            action = "skip"
            reasons.append(f"Moisture {m}% is above the {crop_info['wet']}% wet limit — let the soil breathe")
        elif adj_dr and adj_dr > 0:
            eta_hours = round((m - crop_info["dry"]) / adj_dr, 1)
            if rain_soon and wsum["rain_in_hours"] < eta_hours:
                action = "rain_covers"
                reasons.append(f"Rain in ~{wsum['rain_in_hours']}h ({wsum['total_rain_mm']}mm) arrives before the soil "
                               f"dries (~{eta_hours}h) — nature will water for you, skip this round")
            else:
                action = "water_later"
                reasons.append(f"Moisture {m}% is healthy; at {adj_dr}%/hr it reaches the dry threshold in ~{eta_hours}h")
        else:
            action = "monitor"
            reasons.append(f"Moisture {m}% is healthy; collecting more readings to learn your soil's drying speed")

        if wsum and wsum["max_temp"] is not None and wsum["max_temp"] >= crop_info["heat"]:
            reasons.append(f"Heat alert: forecast peaks at {wsum['max_temp']}°C (limit {crop_info['heat']}°C) — "
                           "water early morning and consider shade")

    headlines = {
        "water_now": "💧 Water now",
        "hold_rain": "🌧️ Hold — rain arriving soon",
        "rain_covers": "🌧️ Skip — rain has it covered",
        "water_later": f"⏳ Water in ~{eta_hours}h" if eta_hours else "⏳ Water later",
        "skip": "🚫 No water needed",
        "monitor": "👀 All healthy — monitoring",
        "no_data": "🔌 Waiting for sensor data",
    }
    eta_ts = (datetime.now() + timedelta(hours=eta_hours)).isoformat(timespec="minutes") if eta_hours else None
    return {
        "action": action,
        "headline": headlines[action],
        "reasons": reasons,
        "eta_hours": eta_hours,
        "eta_ts": eta_ts,
        "moisture": m,
        "drying_rate": dr,
        "weather": wsum,
        "outdoor": outdoor,
        "plant": plant,
        "crop": crop,
    }

@app.get("/api/smart-plan")
async def smart_plan(ai: int = Query(0), user_email: Optional[str] = Query(None)):
    farm = get_user_farm_context(user_email)
    primary = farm["primary_plant"] if farm else None
    plan = compute_smart_plan(primary.get("crop_type") if primary else None,
                              primary.get("name") if primary else None)
    plan["ai_insight"] = None
    if ai and (get_gemini_api_key() or get_ai_api_key()):
        try:
            facts = "; ".join(plan["reasons"]) or "no data"
            messages = [
                {"role": "system", "content":
                    "You are PlantPulse AI. In exactly 2 short, friendly sentences explain this watering "
                    "decision to a beginner and give one practical tip. No lists, no headings."},
                {"role": "user", "content":
                    f"Plant: {plan['plant']} ({plan['crop']}, {(primary or {}).get('growth_stage', 'vegetative')}). "
                    + ((f"Farm: {farm['farm_name']}, {farm['parcel_area']} {farm['parcel_unit']}, about {farm['approximate_plant_count']} plants. ") if farm else "")
                    + f"Decision: {plan['headline']}. Facts: {facts}"},
            ]
            try:
                plan["ai_insight"] = call_gemini_chat(messages)
            except Exception:
                plan["ai_insight"] = call_groq_chat(messages)
        except Exception as e:
            print(f"[PlantPulse] AI insight failed: {e}")
    return plan

daily_weather_cache = {"ts": 0.0, "key": None, "data": None}

def get_daily_forecast(lat, lon):
    now = time.time()
    key = f"{lat},{lon}"
    if daily_weather_cache["data"] and daily_weather_cache["key"] == key and now - daily_weather_cache["ts"] < 1800:
        return daily_weather_cache["data"]
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,precipitation_sum,precipitation_probability_max,"
        "et0_fao_evapotranspiration"
        "&forecast_days=7&timezone=auto"
    )
    r = req_lib.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    daily_weather_cache.update(ts=now, key=key, data=data)
    return data

def compute_week_plan():
    """7-day watering forecast: walks the week forward using live moisture,
    the soil's measured drying rate, and each day's real weather forecast."""
    conn = get_db(); c = conn.cursor()
    latest = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    rows_12h = [dict(r) for r in c.execute(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
        ((datetime.now() - timedelta(hours=12)).isoformat(),)).fetchall()]
    conn.close()
    crop = get_setting("crop_type", "strawberry")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    outdoor = get_setting("plant_outdoor", "true") == "true"
    m = latest["moisture_pct"] if latest else None
    dr = drying_rate(rows_12h)

    days = []
    if m is not None:
        try:
            fc = get_daily_forecast(get_setting("latitude", "19.0760"), get_setting("longitude", "72.8777"))
            d = fc["daily"]
            sim = m
            for i, date in enumerate(d["time"]):
                rain_mm = d["precipitation_sum"][i] or 0
                rain_prob = d["precipitation_probability_max"][i] or 0
                max_temp = d["temperature_2m_max"][i]
                et0 = d["et0_fao_evapotranspiration"][i] or 4.0
                # daily loss: measured drying rate if we have one, else weather-driven estimate
                loss = dr * 24 if dr and dr > 0 else et0 * 1.15 + 1.4
                gain = rain_mm * 3.0 if outdoor else 0
                sim = min(88.0, max(3.0, sim - loss + gain))
                water = sim < crop_info["dry"]
                if water:
                    sim = 82.0
                days.append({
                    "date": date,
                    "water": water,
                    "rain_mm": round(rain_mm, 1),
                    "rain_prob": rain_prob,
                    "max_temp": max_temp,
                    "moisture_end": round(sim, 0),
                })
        except Exception as e:
            print(f"[PlantPulse] Daily forecast failed: {e}")

    return {
        "moisture": m,
        "drying_rate": dr,
        "outdoor": outdoor,
        "crop_dry": crop_info["dry"],
        "days": days,
        "auto": {
            "mode": get_setting("pump_mode", "manual"),
            "on_below": get_setting("pump_auto_on_below", "55"),
            "off_at": get_setting("pump_auto_off_at", "75"),
            "window_start": get_setting("pump_watering_window_start", "06:00"),
            "window_end": get_setting("pump_watering_window_end", "08:00"),
            "max_runtime": get_setting("pump_max_runtime", "300"),
            "temp_max": get_setting("pump_rule_temp_max", ""),
            "humidity_max": get_setting("pump_rule_humidity_max", ""),
            "n_min": get_setting("pump_rule_n_min", ""),
            "p_min": get_setting("pump_rule_p_min", ""),
            "k_min": get_setting("pump_rule_k_min", ""),
            "ph_min": get_setting("pump_rule_ph_min", ""),
            "ph_max": get_setting("pump_rule_ph_max", ""),
            "ec_max": get_setting("pump_rule_ec_max", ""),
            "rain_max": get_setting("pump_rule_rain_max", ""),
            "action_type": get_setting("pump_action_type", "water"),
            "fertilizer_method": get_setting("pump_fertilizer_method", "fertigation"),
            "fertilizer_concentration": get_setting("pump_fertilizer_concentration", ""),
            "fertilizer_tank_liters": get_setting("pump_fertilizer_tank_liters", ""),
        },
    }

@app.get("/api/pump/plan")
async def pump_watering_plan():
    return compute_week_plan()

@app.post("/api/pump/plan/generate")
async def generate_watering_plan():
    """Run the full decision pipeline, ask the AI to summarize it,
    and persist the result as the current AI-suggested plan."""
    plan = compute_week_plan()
    if not plan["days"]:
        raise HTTPException(503, "Weather forecast unavailable — cannot generate a plan right now")
    crop = get_setting("crop_type", "strawberry")
    plant = get_setting("plant_name", "My Plant")
    facts = "; ".join(
        f"{d['date']}: {'WATER' if d['water'] else ('rain ' + str(d['rain_mm']) + 'mm' if d['rain_prob'] >= 60 else 'no water')}, "
        f"max {d['max_temp']}°C, soil ends {d['moisture_end']}%"
        for d in plan["days"]
    )
    ai_summary = None
    if get_gemini_api_key() or get_ai_api_key():
        try:
            messages = [
                {"role": "system", "content":
                    "You are PlantPulse AI, an agronomist. Summarize this 7-day watering plan for a beginner "
                    "in 2-3 friendly sentences: when to water, what the weather does for them, and one tip. "
                    "No lists, no headings."},
                {"role": "user", "content":
                    f"Plant: {plant} ({crop}). Current moisture: {plan['moisture']}%. Week plan: {facts}"},
            ]
            try:
                ai_summary = call_gemini_chat(messages)
            except Exception:
                ai_summary = call_groq_chat(messages)
        except Exception as e:
            print(f"[PlantPulse] Plan AI summary failed: {e}")
    ts = now_ts()
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO watering_plans (created_ts, plan_json, ai_summary) VALUES (?,?,?)",
              (ts, json.dumps(plan["days"]), ai_summary))
    conn.commit(); conn.close()
    return {"created_ts": ts, "days": plan["days"], "ai_summary": ai_summary,
            "moisture": plan["moisture"], "drying_rate": plan["drying_rate"]}

# ─────────────────────────────────────────────
# ANALOG-DAY RAG — retrieval over the farm's own history
# ─────────────────────────────────────────────
FEATURE_KEYS = ["avg_moisture", "min_moisture", "drop", "max_temp", "avg_hum", "month_sin", "month_cos"]
daily_history_cache = {"ts": 0.0, "data": None}

def build_daily_history():
    """Compress every historical day into a feature vector + its 48h outcome."""
    now = time.time()
    if daily_history_cache["data"] and now - daily_history_cache["ts"] < 3600:
        return daily_history_cache["data"]
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT ts, moisture_pct, temp, humidity FROM readings ORDER BY ts").fetchall()
    waterings = [r["ts"] for r in c.execute("SELECT ts FROM watering_log").fetchall()]
    conn.close()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["ts"][:10], []).append(r)
    dates = sorted(by_day)
    hist = []
    for i, d in enumerate(dates):
        rs = by_day[d]
        ms = [x["moisture_pct"] for x in rs if x["moisture_pct"] is not None]
        temps = [x["temp"] for x in rs if x["temp"] is not None]
        hums = [x["humidity"] for x in rs if x["humidity"] is not None]
        if not ms or not temps:
            continue
        month = int(d[5:7])
        feat = {
            "date": d,
            "avg_moisture": sum(ms) / len(ms),
            "min_moisture": min(ms),
            "drop": ms[0] - ms[-1],
            "max_temp": max(temps),
            "avg_hum": sum(hums) / len(hums) if hums else 60.0,
            "month_sin": math.sin(2 * math.pi * month / 12),
            "month_cos": math.cos(2 * math.pi * month / 12),
        }
        nxt = [x for dd in dates[i + 1:i + 3] for x in by_day[dd]]
        nms = [x["moisture_pct"] for x in nxt if x["moisture_pct"] is not None]
        end_date = dates[i + 2] if i + 2 < len(dates) else "9999"
        feat["outcome"] = {
            "min_moisture_next48h": round(min(nms), 1) if nms else None,
            "waterings_next48h": len([w for w in waterings if d < w[:10] <= end_date]),
            "risk_hours_next48h": len([x for x in nxt if x["humidity"] and x["humidity"] > 75]),
        }
        hist.append(feat)
    daily_history_cache.update(ts=now, data=hist)
    return hist

def _feature_stats(hist):
    stats = {}
    for k in FEATURE_KEYS:
        vals = [h[k] for h in hist]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        stats[k] = (mean, math.sqrt(var) or 1.0)
    return stats

def find_analog_days(query_feat, hist, k=5, exclude_date=None):
    """K nearest historical days to the query, z-normalized euclidean distance."""
    usable = [h for h in hist if h["outcome"]["min_moisture_next48h"] is not None
              and h["date"] != exclude_date]
    if len(usable) < k:
        return []
    stats = _feature_stats(usable)
    def dist(h):
        s = 0.0
        for key in FEATURE_KEYS:
            mean, sd = stats[key]
            s += ((h[key] - mean) / sd - (query_feat[key] - mean) / sd) ** 2
        return math.sqrt(s)
    return sorted(usable, key=dist)[:k]


def build_decision_evidence(query_feat, hist, analogs):
    """Return the exact daily aggregates and ranked analogs used for a decision."""
    usable = [h for h in hist if h["outcome"]["min_moisture_next48h"] is not None
              and h["date"] != query_feat.get("date")]
    stats = _feature_stats(usable) if usable else {}

    def distance(day):
        return math.sqrt(sum(
            ((day[key] - stats[key][0]) / stats[key][1]
             - (query_feat[key] - stats[key][0]) / stats[key][1]) ** 2
            for key in FEATURE_KEYS
        )) if stats else 0.0

    feature_labels = {
        "avg_moisture": ("Avg moisture", "%"),
        "min_moisture": ("Min moisture", "%"),
        "drop": ("24h moisture drop", "%"),
        "max_temp": ("Max temperature", "°C"),
        "avg_hum": ("Avg humidity", "%"),
    }
    return {
        "flow": ["Last 24h sensors", "Daily feature summary", "5 closest farm days", "48h outcomes", "AI + safety rules", "Decision"],
        "current_features": [
            {"key": key, "label": label, "value": round(query_feat[key], 1), "unit": unit}
            for key, (label, unit) in feature_labels.items()
        ],
        "matching_note": "Ranked by z-normalized distance across moisture, temperature, humidity, and seasonality.",
        "analogs": [{
            "rank": rank,
            "date": day["date"],
            "distance": round(distance(day), 2),
            "avg_moisture": round(day["avg_moisture"], 1),
            "min_moisture": round(day["min_moisture"], 1),
            "drop": round(day["drop"], 1),
            "max_temp": round(day["max_temp"], 1),
            "avg_hum": round(day["avg_hum"], 1),
            "outcome": day["outcome"],
        } for rank, day in enumerate(analogs, 1)],
    }

def build_today_features():
    """Live feature vector for right now, same shape as historical days."""
    conn = get_db(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        "SELECT ts, moisture_pct, temp, humidity FROM readings WHERE ts >= ? ORDER BY ts",
        ((datetime.now() - timedelta(hours=24)).isoformat(),)).fetchall()]
    conn.close()
    ms = [x["moisture_pct"] for x in rows if x["moisture_pct"] is not None]
    temps = [x["temp"] for x in rows if x["temp"] is not None]
    hums = [x["humidity"] for x in rows if x["humidity"] is not None]
    if not ms:
        return None
    month = datetime.now().month
    fmax_temp = max(temps) if temps else 30.0
    try:
        wsum = summarize_weather_24h(get_weather_forecast(
            get_setting("latitude", "19.0760"), get_setting("longitude", "72.8777")))
        if wsum["max_temp"] is not None:
            fmax_temp = max(fmax_temp, wsum["max_temp"])
    except Exception:
        wsum = None
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "avg_moisture": sum(ms) / len(ms),
        "min_moisture": min(ms),
        "drop": ms[0] - ms[-1],
        "max_temp": fmax_temp,
        "avg_hum": sum(hums) / len(hums) if hums else 60.0,
        "month_sin": math.sin(2 * math.pi * month / 12),
        "month_cos": math.cos(2 * math.pi * month / 12),
        "weather": wsum,
    }

# ─────────────────────────────────────────────
# CROP KNOWLEDGE LIBRARY — retrieved into prompts
# ─────────────────────────────────────────────
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")

def retrieve_knowledge(crop, query, k=2):
    """Tiny keyword-overlap retriever over per-crop markdown chunks."""
    chunks = []
    for fname in (f"{crop}.md", "general.md"):
        path = os.path.join(KNOWLEDGE_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for part in text.split("\n## "):
            part = part.strip()
            if part:
                chunks.append(part if part.startswith("#") else "## " + part)
    if not chunks:
        return []
    q_words = set(re.findall(r"[a-z]+", query.lower()))
    def score(chunk):
        c_words = set(re.findall(r"[a-z]+", chunk.lower()))
        return len(q_words & c_words)
    ranked = sorted(chunks, key=score, reverse=True)
    return [ch for ch in ranked[:k] if score(ch) > 0]

# ─────────────────────────────────────────────
# FARM INSIGHTS — 1 year of data turned into decisions
# ─────────────────────────────────────────────
LITRES_PER_WATERING = 2.0      # per plant, per watering event (potted estimate)
WATER_COST_PER_LITRE = 0.05    # ₹, tanker-water ballpark
FARM_SCALE_PLANTS = 100

@app.get("/api/insights")
async def farm_insights():
    conn = get_db(); c = conn.cursor()
    year_ago = (datetime.now() - timedelta(days=365)).isoformat()
    rows = c.execute("SELECT ts, moisture_pct, temp, humidity FROM readings WHERE ts >= ? ORDER BY ts",
                     (year_ago,)).fetchall()
    waterings = [r["ts"] for r in c.execute("SELECT ts FROM watering_log WHERE ts >= ?", (year_ago,)).fetchall()]
    conn.close()
    crop = get_setting("crop_type", "strawberry")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])

    # ── per-day rollups ──
    days = {}
    for r in rows:
        day = r["ts"][:10]
        d = days.setdefault(day, {"risk_hours": 0, "stress": False, "temps": []})
        if r["humidity"] is not None and r["humidity"] > 75:
            d["risk_hours"] += 1
        if (r["temp"] is not None and r["temp"] >= crop_info["heat"]) or \
           (r["moisture_pct"] is not None and r["moisture_pct"] < crop_info["dry"]):
            d["stress"] = True
        if r["temp"] is not None:
            d["temps"].append(r["temp"])

    # ── monthly aggregates ──
    months = {}
    for day, d in days.items():
        mk = day[:7]
        m = months.setdefault(mk, {"risk_days": 0, "stress_days": 0, "waterings": 0,
                                   "temp_ok_hours": 0, "temp_hours": 0})
        if d["risk_hours"] >= 6:
            m["risk_days"] += 1
        if d["stress"]:
            m["stress_days"] += 1
        for t in d["temps"]:
            m["temp_hours"] += 1
            if crop_info["temp_min"] <= t <= crop_info["temp_max"]:
                m["temp_ok_hours"] += 1
    for w in waterings:
        mk = w[:7]
        if mk in months:
            months[mk]["waterings"] += 1
    month_list = [{"month": mk, **v,
                   "planting_score": round(v["temp_ok_hours"] / v["temp_hours"] * 100) if v["temp_hours"] else 0}
                  for mk, v in sorted(months.items())]

    # ── savings: actual waterings vs naive water-every-day (last 30 days) ──
    d30 = (datetime.now() - timedelta(days=30)).isoformat()
    actual_30 = len([w for w in waterings if w >= d30])
    skipped_30 = max(0, 30 - actual_30)
    litres_saved = round(skipped_30 * LITRES_PER_WATERING, 1)
    savings = {
        "actual_waterings": actual_30,
        "skipped_waterings": skipped_30,
        "litres_saved_per_plant": litres_saved,
        "rupees_saved_per_plant": round(litres_saved * WATER_COST_PER_LITRE, 2),
        "farm_scale_plants": FARM_SCALE_PLANTS,
        "farm_litres_saved": round(litres_saved * FARM_SCALE_PLANTS),
        "farm_rupees_saved": round(litres_saved * WATER_COST_PER_LITRE * FARM_SCALE_PLANTS),
    }

    # ── disease risk: next 72h humidity streaks from real forecast ──
    disease = {"risk": False, "message": "No fungal-risk window in the next 3 days 🎉", "streak_hours": 0}
    try:
        w = get_weather_forecast(get_setting("latitude", "19.0760"), get_setting("longitude", "72.8777"))
        hums = w["hourly"]["relative_humidity_2m"]
        times = w["hourly"]["time"]
        now_hour = datetime.now().strftime("%Y-%m-%dT%H:00")
        idx = times.index(now_hour) if now_hour in times else 0
        streak, best_streak, best_start = 0, 0, None
        for i in range(idx, min(idx + 72, len(hums))):
            if hums[i] is not None and hums[i] > 75:
                if streak == 0:
                    start = times[i]
                streak += 1
                if streak > best_streak:
                    best_streak, best_start = streak, start
            else:
                streak = 0
        if best_streak >= 6:
            day_name = datetime.fromisoformat(best_start).strftime("%A %H:%M")
            disease = {"risk": True, "streak_hours": best_streak,
                       "message": f"⚠️ Humidity stays above 75% for ~{best_streak}h starting {day_name} — "
                                  "high fungal risk. Improve airflow, avoid wetting leaves."}
    except Exception as e:
        print(f"[PlantPulse] Disease forecast failed: {e}")

    # ── best planting window: top months by hours-in-ideal-temp-range ──
    ranked = sorted([m for m in month_list if m["temp_hours"] > 100],
                    key=lambda m: m["planting_score"], reverse=True)
    best_months = [{"month": m["month"], "score": m["planting_score"]} for m in ranked[:3]]

    return {
        "crop": crop,
        "crop_name": crop_info["name"],
        "months": month_list,
        "savings": savings,
        "disease": disease,
        "best_months": best_months,
        "data_days": len(days),
    }

# ─────────────────────────────────────────────
# SMART ADVICE — motor timing & fertilizer choice, with reasons
# ─────────────────────────────────────────────
STAGE_NUTRIENT_TARGETS = {
    # mg/kg targets per growth stage: (N_min, N_max, P_min, P_max, K_min, K_max)
    "seedling":   (30, 50, 15, 30, 100, 180),
    "vegetative": (40, 60, 15, 30, 120, 200),
    "flowering":  (25, 45, 20, 40, 150, 250),
    "fruiting":   (20, 40, 20, 40, 180, 280),
}
FERTILIZER_FOR = {
    "N": "Urea or blood meal (nitrogen boost)",
    "P": "Bone meal or DAP (phosphorus boost)",
    "K": "Sulfate of potash / MOP (potassium boost)",
}

@app.get("/api/advice/motor")
async def motor_advice():
    conn = get_db(); c = conn.cursor()
    latest = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    rows_12h = [dict(r) for r in c.execute(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
        ((datetime.now() - timedelta(hours=12)).isoformat(),)).fetchall()]
    conn.close()
    m = latest["moisture_pct"] if latest else None
    dr = drying_rate(rows_12h)
    on_below = float(get_setting("pump_auto_on_below", "55"))
    off_at = float(get_setting("pump_auto_off_at", "75"))
    ws = get_setting("pump_watering_window_start", "06:00")
    we = get_setting("pump_watering_window_end", "08:00")
    outdoor = get_setting("plant_outdoor", "true") == "true"
    reasons = []
    if m is None:
        return {"headline": "🔌 No sensor data", "reasons": ["Connect the ESP8266 to plan motor runs"], "start_at": None}
    wsum = None
    try:
        wsum = summarize_weather_24h(get_weather_forecast(
            get_setting("latitude", "19.0760"), get_setting("longitude", "72.8777")))
    except Exception:
        pass
    rain_soon = bool(outdoor and wsum and wsum["rain_in_hours"] is not None and wsum["total_rain_mm"] >= 2)
    deficit = max(0.0, off_at - m)
    est_minutes = round(max(1.0, deficit * 0.4), 1)  # rough: ~0.4 min pumping per % deficit

    now = datetime.now()
    def next_window_start():
        h, mm = map(int, ws.split(":"))
        cand = now.replace(hour=h, minute=mm, second=0, microsecond=0)
        if cand < now:
            cand += timedelta(days=1)
        return cand

    if m <= on_below:
        if rain_soon and wsum["rain_in_hours"] <= 2:
            headline = "🌧️ Hold the motor — rain almost here"
            start_at = None
            reasons.append(f"Soil is at {m}% (below your {on_below}% trigger) BUT {wsum['total_rain_mm']}mm rain is expected within {wsum['rain_in_hours']}h")
            reasons.append("Running the motor now would waste water and electricity")
        else:
            in_window = ws <= now.strftime("%H:%M") <= we
            start_at = "now" if in_window else next_window_start().strftime("%Y-%m-%d %H:%M")
            headline = f"⚡ Start motor {'NOW' if in_window else 'at ' + next_window_start().strftime('%H:%M')} for ~{est_minutes} min"
            reasons.append(f"Moisture {m}% is at/below your {on_below}% start trigger")
            reasons.append(f"~{est_minutes} min brings soil from {m}% to your {off_at}% stop target")
            if not in_window:
                reasons.append(f"Waiting for your watering window ({ws}–{we}) — early morning watering loses least to evaporation")
    elif dr and dr > 0:
        eta_h = round((m - on_below) / dr, 1)
        eta_time = now + timedelta(hours=eta_h)
        if rain_soon and wsum["rain_in_hours"] < eta_h:
            headline = "🌧️ No motor needed — rain arrives first"
            start_at = None
            reasons.append(f"Soil {m}% won't hit your {on_below}% trigger for ~{eta_h}h at the current drying rate ({dr}%/hr)")
            reasons.append(f"{wsum['total_rain_mm']}mm rain is forecast in ~{wsum['rain_in_hours']}h — free irrigation")
        else:
            start_at = eta_time.strftime("%Y-%m-%d %H:%M")
            headline = f"⏳ Motor needed in ~{eta_h}h ({eta_time.strftime('%a %H:%M')})"
            reasons.append(f"Moisture {m}% is above your {on_below}% trigger — no need yet")
            reasons.append(f"At the measured drying rate of {dr}%/hr it reaches the trigger around {eta_time.strftime('%H:%M')}")
            if wsum and wsum["max_temp"] is not None:
                reasons.append(f"Forecast max {wsum['max_temp']}°C and {wsum['max_rain_prob']}% rain chance factored in")
    else:
        headline = "👀 Motor idle — soil healthy"
        start_at = None
        reasons.append(f"Moisture {m}% is comfortably above your {on_below}% trigger")
        reasons.append("Still learning your soil's drying speed for a precise start time")
    return {"headline": headline, "reasons": reasons, "start_at": start_at,
            "moisture": m, "trigger": on_below, "target": off_at, "est_minutes": est_minutes}

@app.get("/api/advice/fertilizer")
async def fertilizer_advice(user_email: Optional[str] = Query(None)):
    conn = get_db(); c = conn.cursor()
    latest = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()
    crop = get_setting("crop_type", "strawberry")
    stage = get_setting("growth_stage", "vegetative")
    farm = get_user_farm_context(user_email)
    if farm and farm["primary_plant"]:
        crop = farm["primary_plant"].get("crop_type") or crop
        stage = farm["primary_plant"].get("growth_stage") or stage
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    if not latest or latest["n_val"] is None:
        return {"headline": "🧪 Waiting for NPK sensor data",
                "reasons": ["Add the NPK/pH/EC sensors (or run the demo seeder) to unlock fertilizer advice"],
                "sensors": None}
    n, p, k = latest["n_val"], latest["p_val"], latest["k_val"]
    ph, ec = latest["ph"], latest["ec"]
    m = latest["moisture_pct"]
    t = latest["temp"]
    tgt = STAGE_NUTRIENT_TARGETS.get(stage, STAGE_NUTRIENT_TARGETS["vegetative"])
    reasons, deficits = [], []
    if n < tgt[0]:
        deficits.append("N"); reasons.append(f"Nitrogen {n:.0f} mg/kg is below the {tgt[0]}–{tgt[1]} target for the {stage} stage")
    if p < tgt[2]:
        deficits.append("P"); reasons.append(f"Phosphorus {p:.0f} mg/kg is below the {tgt[2]}–{tgt[3]} target for {stage}")
    if k < tgt[4]:
        deficits.append("K"); reasons.append(f"Potassium {k:.0f} mg/kg is below the {tgt[4]}–{tgt[5]} target for {stage}")

    blockers = []
    if ph is not None and ph < 5.5:
        blockers.append(f"Soil pH {ph:.1f} is too acidic — nutrients get locked; add garden lime first, then re-test")
    elif ph is not None and ph > 7.2:
        blockers.append(f"Soil pH {ph:.1f} is too alkaline — iron/phosphorus lock out; add elemental sulfur or compost")
    if ec is not None and ec > 2.5:
        blockers.append(f"EC {ec:.2f} mS/cm is high — salts are building up; flush with plain water, do NOT fertilize now")
    if m is not None and m < crop_info["dry"]:
        blockers.append(f"Soil is dry ({m}%) — fertilizing dry soil burns roots; water first, feed tomorrow")
    if t is not None and t < 12:
        blockers.append(f"Root zone {t}°C is cold — uptake is minimal, feeding now is wasted money")

    if blockers:
        headline = "⛔ Fix soil first — don't fertilize yet"
        reasons = blockers + reasons
    elif deficits:
        headline = "🧪 Feed: " + FERTILIZER_FOR[deficits[0]]
        if len(deficits) > 1:
            reasons.append("Multiple deficits — a balanced NPK covers all: " +
                           ", ".join(FERTILIZER_FOR[d] for d in deficits[1:]))
        if ec is not None and ec < 0.8:
            reasons.append(f"EC {ec:.2f} mS/cm confirms overall nutrition is weak")
        reasons.append("Apply in the morning after light watering, at half the packet dose for potted plants")
    else:
        headline = "✅ Nutrition is on target — no fertilizer needed"
        reasons.append(f"N {n:.0f} / P {p:.0f} / K {k:.0f} mg/kg all within the {stage}-stage targets")
        if ph is not None:
            reasons.append(f"pH {ph:.1f} is in the sweet spot — nutrients fully available")
    return {"headline": headline, "reasons": reasons,
            "sensors": {"n": round(n, 1), "p": round(p, 1), "k": round(k, 1),
                        "ph": round(ph, 2) if ph is not None else None,
                        "ec": round(ec, 2) if ec is not None else None},
            "stage": stage, "deficits": deficits}

# ─────────────────────────────────────────────
# ML MODELS — XGBoost trained on this farm's history
# ─────────────────────────────────────────────
import ml_engine

@app.post("/api/ml/train")
async def ml_train():
    crop = get_setting("crop_type", "strawberry")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    try:
        return ml_engine.train_all(crop_info["dry"])
    except Exception as e:
        raise HTTPException(500, f"Training failed: {e}")

@app.get("/api/ml/predict")
async def ml_predict():
    crop = get_setting("crop_type", "strawberry")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    try:
        pred = ml_engine.predict_today(crop_info)
    except Exception as e:
        raise HTTPException(500, f"Prediction failed: {e}")
    if pred is None:
        raise HTTPException(503, "Not enough data to train the ML models yet")
    return pred

# ─────────────────────────────────────────────
# AI DAILY DECISION — LLM decides, grounded in analog days; rules veto
# ─────────────────────────────────────────────
class FeedbackIn(BaseModel):
    decision_id: int
    verdict: str  # "up" | "down"
    note: Optional[str] = None

def apply_guardrails(decision, moisture, crop_info):
    notes = []
    if decision.get("water_today") and moisture is not None and moisture > crop_info["wet"]:
        decision["water_today"] = False
        decision["water_time"] = "none"
        notes.append(f"Guardrail: soil {moisture}% is above the {crop_info['wet']}% wet limit — watering vetoed")
    amt = decision.get("water_amount_ml") or 0
    if amt > 1000:
        decision["water_amount_ml"] = 1000
        notes.append("Guardrail: water amount capped at 1000ml")
    if not isinstance(decision.get("confidence"), (int, float)) or not 0 <= decision.get("confidence", -1) <= 100:
        decision["confidence"] = 60
    return decision, notes

def parse_llm_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])

@app.post("/api/ai/decision/generate")
async def generate_ai_decision(user_email: Optional[str] = Query(None)):
    today = build_today_features()
    if today is None:
        raise HTTPException(503, "No sensor data in the last 24h — cannot make a decision")
    crop = get_setting("crop_type", "strawberry")
    plant = get_setting("plant_name", "My Plant")
    stage = get_setting("growth_stage", "vegetative")
    farm = get_user_farm_context(user_email)
    if farm and farm["primary_plant"]:
        primary = farm["primary_plant"]
        plant = primary.get("name") or plant
        crop = primary.get("crop_type") or crop
        stage = primary.get("growth_stage") or stage
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    hist = build_daily_history()
    analogs = find_analog_days(today, hist, k=5, exclude_date=today["date"])

    analog_lines = []
    for a in analogs:
        o = a["outcome"]
        analog_lines.append(
            f"- {a['date']}: moisture avg {a['avg_moisture']:.0f}% (min {a['min_moisture']:.0f}%), "
            f"max {a['max_temp']:.0f}°C, humidity {a['avg_hum']:.0f}% → next 48h: "
            f"moisture fell to {o['min_moisture_next48h']}%, {o['waterings_next48h']} watering(s), "
            f"{o['risk_hours_next48h']}h of fungal-risk humidity")

    # feedback memory: corrections given on similar past days become few-shot guidance
    conn = get_db(); c = conn.cursor()
    if user_email:
        fb_rows = c.execute("""SELECT f.verdict, f.note, f.features_json FROM ai_feedback f
                               JOIN ai_decisions d ON d.id=f.decision_id
                               WHERE d.user_email=? ORDER BY f.id DESC LIMIT 50""",
                            (user_email.strip().lower(),)).fetchall()
    else:
        fb_rows = c.execute("""SELECT f.verdict, f.note, f.features_json FROM ai_feedback f
                               JOIN ai_decisions d ON d.id=f.decision_id
                               WHERE d.user_email IS NULL ORDER BY f.id DESC LIMIT 50""").fetchall()
    conn.close()
    fb_lines = []
    if fb_rows:
        fb_feats = []
        for r in fb_rows:
            try:
                ft = json.loads(r["features_json"])
                ft["outcome"] = {"min_moisture_next48h": 0}
                fb_feats.append((ft, r["verdict"], r["note"]))
            except Exception:
                pass
        if fb_feats:
            stats = _feature_stats([f for f, _, _ in fb_feats] + [today])
            def fdist(ft):
                return math.sqrt(sum(((ft[k] - stats[k][0]) / stats[k][1] - (today[k] - stats[k][0]) / stats[k][1]) ** 2
                                     for k in FEATURE_KEYS))
            for ft, verdict, note in sorted(fb_feats, key=lambda x: fdist(x[0]))[:2]:
                fb_lines.append(f"- On a similar day the farmer said {'👍 good call' if verdict == 'up' else '👎 wrong'}"
                                + (f": \"{note}\"" if note else ""))

    know = retrieve_knowledge(crop, "watering irrigation disease humidity heat fertilizer", k=1)
    w = today.get("weather") or {}
    ml_pred = None
    try:
        ml_pred = ml_engine.predict_today(crop_info)
    except Exception as e:
        print(f"[PlantPulse] ML prediction unavailable for decision: {e}")
    ml_block = ""
    if ml_pred:
        ml_block = (
            "\n\nML MODEL PREDICTIONS (XGBoost trained on this farm's own year of data):\n"
            f"- Probability soil needs water within 24h: {ml_pred['irrigation_probability_pct']}%\n"
            f"- Predicted moisture loss next 24h: {ml_pred['predicted_moisture_drop_24h_pct']}%"
            f" (suggested amount if watering: {ml_pred['suggested_water_ml']}ml)\n"
            f"- Fungal disease risk within 48h: {ml_pred['disease_probability_pct']}%\n"
            f"- Sensor anomalies in last 24h: {ml_pred['sensor_anomalies_24h']}\n"
            f"- Yield outlook index: {ml_pred['yield_index']}/100 ({ml_pred['yield_grade']})"
        )
    prompt_user = (
        f"Plant: {plant} ({crop}, {stage} stage; thresholds: dry<{crop_info['dry']}%, wet>{crop_info['wet']}%, heat>{crop_info['heat']}°C).\n"
        + ((f"Farm profile: {farm['farm_name']}, parcel {farm['parcel_name']} ({farm['parcel_area']} {farm['parcel_unit']}), "
             f"about {farm['approximate_plant_count']} plants. Other registered plants: "
             + ", ".join(f"{p.get('name')} ({p.get('crop_type')}, {p.get('growth_stage', 'vegetative')}, qty {p.get('quantity', 1)})" for p in farm['plants']) + ".\n") if farm else "") +
        f"TODAY: moisture avg {today['avg_moisture']:.0f}% (min {today['min_moisture']:.0f}%), 24h drop {today['drop']:.1f}%, "
        f"max temp {today['max_temp']:.0f}°C, humidity {today['avg_hum']:.0f}%.\n"
        f"Forecast next 24h: rain {w.get('max_rain_prob', '?')}% ({w.get('total_rain_mm', '?')}mm), up to {w.get('max_temp', '?')}°C.\n\n"
        f"THE 5 MOST SIMILAR DAYS IN THIS FARM'S HISTORY AND WHAT FOLLOWED:\n" + "\n".join(analog_lines) +
        ml_block +
        (("\n\nFARMER FEEDBACK ON SIMILAR DAYS:\n" + "\n".join(fb_lines)) if fb_lines else "") +
        (("\n\nCROP CARE NOTES:\n" + know[0][:600]) if know else "") +
        "\n\nDecide today's plan, weighing the ML predictions against the analog days. Reply with ONLY this JSON:\n"
        '{"water_today": true/false, "water_time": "morning"/"evening"/"now"/"none", '
        '"water_amount_ml": number, "disease_action": "one short sentence", '
        '"fertilizer_advice": "one short sentence for the current growth stage", '
        '"profit_tip": "one short sentence", "confidence": 0-100, '
        '"reasoning": "2 short sentences citing the similar days and ML predictions"}'
    )
    messages = [
        {"role": "system", "content":
            "You are PlantPulse AI, an irrigation decision agent. Decide based on the farm's own historical "
            "analog days and live data. Be decisive and practical. Output ONLY valid JSON, no markdown."},
        {"role": "user", "content": prompt_user},
    ]
    decision, provider = None, None
    for call, name in ((call_gemini_chat, "gemini"), (call_groq_chat, "groq")):
        try:
            decision = parse_llm_json(call(messages))
            provider = name
            break
        except Exception as e:
            print(f"[PlantPulse] AI decision via {name} failed: {e}")
    if decision is None:
        # deterministic fallback: analog-day majority vote
        votes = sum(1 for a in analogs if a["outcome"]["min_moisture_next48h"] < crop_info["dry"])
        will_dry = votes >= 3
        decision = {
            "water_today": will_dry and today["avg_moisture"] < crop_info["wet"],
            "water_time": "morning" if will_dry else "none",
            "water_amount_ml": 250 if will_dry else 0,
            "disease_action": "Keep leaves dry and airflow high." if today["avg_hum"] > 75 else "No action needed.",
            "profit_tip": "Water early morning to minimise evaporation loss.",
            "confidence": round(max(votes, 5 - votes) / 5 * 100),
            "reasoning": f"{votes} of 5 similar historical days dried below threshold within 48h (offline analog vote).",
        }
        provider = "analog-vote"

    decision, guardrail_notes = apply_guardrails(decision, today["avg_moisture"], crop_info)
    ts = now_ts()
    features_for_db = {k: today[k] for k in FEATURE_KEYS}
    features_for_db["date"] = today["date"]
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO ai_decisions (created_ts, day_key, features_json, decision_json, provider, user_email) VALUES (?,?,?,?,?,?)",
              (ts, today["date"], json.dumps(features_for_db), json.dumps(decision), provider,
               user_email.strip().lower() if user_email else None))
    decision_id = c.lastrowid
    conn.commit(); conn.close()
    evidence = build_decision_evidence(today, hist, analogs)
    return {"id": decision_id, "created_ts": ts, "decision": decision, "provider": provider,
            "guardrails": guardrail_notes,
            "analogs": evidence["analogs"], "evidence": evidence,
            "feedback_used": len(fb_lines)}

@app.get("/api/ai/decision/latest")
async def latest_ai_decision(user_email: Optional[str] = Query(None)):
    conn = get_db(); c = conn.cursor()
    if user_email:
        row = c.execute("SELECT * FROM ai_decisions WHERE user_email=? ORDER BY id DESC LIMIT 1",
                        (user_email.strip().lower(),)).fetchone()
    else:
        row = c.execute("SELECT * FROM ai_decisions WHERE user_email IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"decision": None}
    features = json.loads(row["features_json"])
    hist = build_daily_history()
    analogs = find_analog_days(features, hist, k=5, exclude_date=features.get("date"))
    evidence = build_decision_evidence(features, hist, analogs)
    return {"id": row["id"], "created_ts": row["created_ts"], "day_key": row["day_key"],
            "decision": json.loads(row["decision_json"]), "provider": row["provider"],
            "analogs": evidence["analogs"], "evidence": evidence}

@app.post("/api/ai/feedback")
async def ai_feedback(fb: FeedbackIn):
    if fb.verdict not in ("up", "down"):
        raise HTTPException(400, "verdict must be 'up' or 'down'")
    conn = get_db(); c = conn.cursor()
    dec = c.execute("SELECT features_json FROM ai_decisions WHERE id=?", (fb.decision_id,)).fetchone()
    if not dec:
        conn.close()
        raise HTTPException(404, "Decision not found")
    c.execute("INSERT INTO ai_feedback (decision_id, ts, verdict, note, features_json) VALUES (?,?,?,?,?)",
              (fb.decision_id, now_ts(), fb.verdict, (fb.note or "").strip()[:200], dec["features_json"]))
    conn.commit(); conn.close()
    return {"status": "ok", "message": "Feedback saved — the AI will remember this on similar days"}

@app.get("/api/ai/backtest")
async def ai_backtest():
    """Leave-one-out KNN backtest over the farm's own history:
    predict 'will the next 48h need watering?' from the 5 nearest other days."""
    hist = build_daily_history()
    crop = get_setting("crop_type", "strawberry")
    dry = CROP_DB.get(crop, CROP_DB["strawberry"])["dry"]
    usable = [h for h in hist if h["outcome"]["min_moisture_next48h"] is not None]
    if len(usable) < 30:
        return {"ready": False, "message": "Need at least 30 days of data to backtest"}
    total = correct = caught = missed = false_alarm = needed_total = 0
    for h in usable:
        truth = h["outcome"]["min_moisture_next48h"] < dry
        neighbors = find_analog_days(h, usable, k=5, exclude_date=h["date"])
        votes = sum(1 for n in neighbors if n["outcome"]["min_moisture_next48h"] < dry)
        pred = votes >= 3
        total += 1
        if truth:
            needed_total += 1
        if pred == truth:
            correct += 1
        if pred and truth:
            caught += 1
        elif not pred and truth:
            missed += 1
        elif pred and not truth:
            false_alarm += 1
    return {
        "ready": True,
        "days_tested": total,
        "accuracy_pct": round(correct / total * 100, 1),
        "dryouts_total": needed_total,
        "dryouts_caught": caught,
        "dryouts_missed": missed,
        "false_alarms": false_alarm,
        "model": "k-nearest-neighbors (k=5) over this farm's own daily history, leave-one-out",
    }

@app.get("/api/pump/plan/saved")
async def get_saved_watering_plan():
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT * FROM watering_plans ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"plan": None}
    return {"plan": {"created_ts": row["created_ts"], "days": json.loads(row["plan_json"]),
                     "ai_summary": row["ai_summary"]}}

# ─────────────────────────────────────────────
# USERS (simple identification — no passwords for now)
# ─────────────────────────────────────────────
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]{2,}$")
PBKDF2_ITERATIONS = 100_000

def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"{salt}${digest}"

def verify_password(password, stored):
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return hmac.compare_digest(calc, digest)

def _user_dict(row):
    conn = get_db(); c = conn.cursor()
    profile = c.execute("SELECT completed FROM user_farm_profiles WHERE user_email=?", (row["email"],)).fetchone()
    conn.close()
    return {"id": row["id"], "email": row["email"], "first_name": row["first_name"],
            "profile_complete": bool(profile and profile["completed"])}


def get_user_farm_context(user_email):
    if not user_email:
        return None
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT * FROM user_farm_profiles WHERE user_email=?",
                    (user_email.strip().lower(),)).fetchone()
    conn.close()
    if not row:
        return None
    plants = json.loads(row["plants_json"] or "[]")
    return {
        "farm_name": row["farm_name"], "parcel_name": row["parcel_name"],
        "parcel_area": row["parcel_area"], "parcel_unit": row["parcel_unit"],
        "approximate_plant_count": row["approximate_plant_count"],
        "plants": plants, "primary_plant": plants[0] if plants else None,
    }


@app.get("/api/profile/{user_email}")
async def get_farm_profile(user_email: str):
    email = user_email.strip().lower()
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT * FROM user_farm_profiles WHERE user_email=?", (email,)).fetchone()
    conn.close()
    if not row:
        return {"profile_complete": False, "profile": None}
    profile = dict(row)
    profile["plants"] = json.loads(profile.pop("plants_json") or "[]")
    profile["profile_complete"] = bool(profile.pop("completed"))
    return {"profile_complete": profile["profile_complete"], "profile": profile}


@app.post("/api/profile")
async def save_farm_profile(payload: FarmProfileUpdate):
    email = payload.user_email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid user email")
    if payload.parcel_area <= 0 or payload.approximate_plant_count <= 0:
        raise HTTPException(400, "Parcel area and plant count must be greater than zero")
    plants = [p.dict() for p in payload.plants if p.name.strip() and p.quantity > 0]
    if not plants:
        raise HTTPException(400, "Add at least one plant")
    conn = get_db(); c = conn.cursor()
    if not c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        conn.close(); raise HTTPException(404, "User not found")
    c.execute("""INSERT INTO user_farm_profiles
        (user_email,farm_name,parcel_name,parcel_area,parcel_unit,approximate_plant_count,plants_json,completed,updated_ts)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_email) DO UPDATE SET farm_name=excluded.farm_name, parcel_name=excluded.parcel_name,
        parcel_area=excluded.parcel_area, parcel_unit=excluded.parcel_unit,
        approximate_plant_count=excluded.approximate_plant_count, plants_json=excluded.plants_json,
        completed=1, updated_ts=excluded.updated_ts""",
        (email, payload.farm_name.strip()[:80], payload.parcel_name.strip()[:80], payload.parcel_area,
         payload.parcel_unit, payload.approximate_plant_count, json.dumps(plants), 1, now_ts()))
    conn.commit(); conn.close()
    return {"status": "ok", "profile_complete": True}

@app.post("/api/auth/signup")
async def auth_signup(payload: UserSignup):
    email = payload.email.strip().lower()
    first_name = " ".join(payload.first_name.split()).strip()[:40]
    password = payload.password
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address")
    if not first_name:
        raise HTTPException(400, "Please enter your first name")
    if len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    conn = get_db(); c = conn.cursor()
    existing = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(409, "This email is already registered — please log in instead")
    ts = now_ts()
    c.execute("INSERT INTO users (email, first_name, password_hash, created_ts, last_login_ts) VALUES (?,?,?,?,?)",
              (email, first_name, hash_password(password), ts, ts))
    conn.commit()
    user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return {"user": _user_dict(user)}

@app.post("/api/auth/login")
async def auth_login(payload: UserLogin):
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address")
    conn = get_db(); c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Email not found — please sign up first")
    if not verify_password(payload.password, user["password_hash"]):
        conn.close()
        raise HTTPException(401, "Wrong password — please try again")
    c.execute("UPDATE users SET last_login_ts=? WHERE id=?", (now_ts(), user["id"]))
    conn.commit(); conn.close()
    return {"user": _user_dict(user)}

@app.post("/api/auth/change-password")
async def auth_change_password(payload: PasswordChange):
    email = payload.email.strip().lower()
    if len(payload.new_password) < 4:
        raise HTTPException(400, "New password must be at least 4 characters")
    conn = get_db(); c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")
    if not verify_password(payload.old_password, user["password_hash"]):
        conn.close()
        raise HTTPException(401, "Current password is wrong")
    c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(payload.new_password), user["id"]))
    conn.commit(); conn.close()
    return {"status": "ok", "message": "Password changed"}

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.post("/api/ingest")
async def ingest(reading: SensorReading, request: Request = None):
    conn = get_db(); c = conn.cursor()
    ts = datetime.now().isoformat(timespec="seconds")
    air = int(get_setting("air_value", "900"))
    water = int(get_setting("water_value", "350"))
    raw = reading.raw
    pct = reading.moisture_pct
    if raw is not None and pct is None:
        pct = moisture_pct(raw, air, water)
    scale = round(pct / 10, 1) if pct is not None else None
    c.execute("""INSERT INTO readings (ts,raw,moisture_pct,scale,temp,humidity,light,source,note,n_val,p_val,k_val,ph,ec)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ts, raw, pct, scale, reading.temp, reading.humidity, reading.light,
               reading.source or "wifi", reading.note,
               reading.n_val, reading.p_val, reading.k_val, reading.ph, reading.ec))
    conn.commit()
    # anomaly checks
    rows = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 20").fetchall()
    rows = [dict(r) for r in rows][::-1]
    anomalies = detect_anomalies(rows)
    crop = get_setting("crop_type", "strawberry")
    ntfy = get_setting("ntfy_topic", "")
    plant = get_setting("plant_name", "Plant")
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    # threshold alerts
    if pct is not None:
        if pct < crop_info["dry"]:
            msg = f"{plant} needs water! Moisture: {pct}%"
            c.execute("INSERT INTO anomalies VALUES (NULL,?,?,?,?,?)",
                      (ts,"dry_soil","error",msg,pct))
            send_ntfy(ntfy, f"🪴 {plant} — Dry Soil", msg, "high")
        elif pct > crop_info["wet"]:
            msg = f"{plant} may be overwatered. Moisture: {pct}%"
            c.execute("INSERT INTO anomalies VALUES (NULL,?,?,?,?,?)",
                      (ts,"overwatered","warning",msg,pct))
            send_ntfy(ntfy, f"🪴 {plant} — Overwatered", msg)
    if reading.temp and reading.temp > crop_info["heat"]:
        msg = f"{plant} too hot! Temp: {reading.temp}°C"
        c.execute("INSERT INTO anomalies VALUES (NULL,?,?,?,?,?)",
                  (ts,"too_hot","warning",msg,reading.temp))
        send_ntfy(ntfy, f"🌡️ {plant} — Too Hot", msg, "high")
    for an in anomalies[-3:]:
        c.execute("INSERT INTO anomalies VALUES (NULL,?,?,?,?,?)",
                  (ts,an["type"],an["severity"],an["message"],an["value"]))
    # watering auto-detect
    if len(rows) >= 2:
        prev_m = rows[-2]["moisture_pct"]
        if pct and prev_m and (pct - prev_m) >= 15:
            c.execute("INSERT INTO watering_log VALUES (NULL,?,?,?,?,?)",
                      (ts,"auto_detected",prev_m,pct,"Auto-detected watering event"))
    conn.commit(); conn.close()
    return {"status":"ok","ts":ts,"moisture_pct":pct}


@app.get("/api/dashboard")
async def dashboard(user_email: Optional[str] = Query(None)):
    conn = get_db(); c = conn.cursor()
    latest = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    recent = c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 5").fetchall()
    recent = [dict(r) for r in recent][::-1]
    crop = get_setting("crop_type","strawberry")
    plant_name = get_setting("plant_name","My Plant")
    stage = get_setting("growth_stage","vegetative")
    if user_email:
        profile_row = c.execute("SELECT plants_json FROM user_farm_profiles WHERE user_email=?",
                                (user_email.strip().lower(),)).fetchone()
        if profile_row:
            try:
                primary_plant = json.loads(profile_row["plants_json"] or "[]")[0]
                plant_name = primary_plant.get("name") or plant_name
                crop = primary_plant.get("crop_type") or crop
                stage = primary_plant.get("growth_stage") or stage
            except (ValueError, IndexError, TypeError):
                pass
    crop_info = CROP_DB.get(crop, CROP_DB["strawberry"])
    m = dict(latest) if latest else {
        "ts": datetime.now().isoformat(timespec="seconds"), "raw": 515, "moisture_pct": 70.0,
        "scale": 7.0, "temp": 26.0, "humidity": 68.0, "light": 62.0,
        "n_val": 42.0, "p_val": 35.0, "k_val": 118.0, "ph": 6.4, "ec": 1.25,
        "source": "mock-profile", "note": "Demo values until hardware is connected"
    }
    # Stable per-user demo variation; hardware readings pass through unchanged.
    if user_email and str(m.get("source") or "").lower() not in ("wifi", "esp8266", "hardware"):
        seed = int(hashlib.sha256(user_email.strip().lower().encode()).hexdigest()[:8], 16)
        offsets = {
            "moisture_pct": ((seed % 9) - 4) * 0.7,
            "temp": (((seed >> 4) % 7) - 3) * 0.3,
            "humidity": (((seed >> 7) % 9) - 4) * 0.6,
            "n_val": ((seed >> 10) % 7) - 3,
            "p_val": ((seed >> 13) % 7) - 3,
            "k_val": ((seed >> 16) % 9) - 4,
            "ph": (((seed >> 19) % 5) - 2) * 0.05,
            "ec": (((seed >> 22) % 5) - 2) * 0.03,
        }
        for key, offset in offsets.items():
            if m.get(key) is not None:
                m[key] = round(m[key] + offset, 2)
        if m.get("moisture_pct") is not None:
            m["scale"] = round(m["moisture_pct"] / 10, 1)
    status, status_color = get_plant_status(m.get("moisture_pct"), m.get("temp"), m.get("humidity"), crop)
    sensor_health = check_sensor_health([dict(r) for r in c.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 20").fetchall()])
    last_water = c.execute("SELECT ts FROM watering_log ORDER BY ts DESC LIMIT 1").fetchone()
    anomaly_count = c.execute("SELECT COUNT(*) FROM anomalies WHERE ts >= ?",
        ((datetime.now()-timedelta(hours=24)).isoformat(),)).fetchone()[0]
    # drying rate
    rows_6h = [dict(r) for r in c.execute("SELECT * FROM readings WHERE ts >= ? ORDER BY ts",
               ((datetime.now()-timedelta(hours=6)).isoformat(),)).fetchall()]
    dr = drying_rate(rows_6h)
    next_water_hrs = None
    if dr and dr > 0 and m.get("moisture_pct"):
        dry_thresh = crop_info["dry"]
        moisture_gap = m["moisture_pct"] - dry_thresh
        next_water_hrs = round(moisture_gap / dr, 1) if moisture_gap > 0 else 0
    pump_mode = get_setting("pump_mode","manual")
    pump_enabled = get_setting("pump_enabled","false")
    conn.close()
    return {
        "plant_name": plant_name,
        "crop_type": crop,
        "crop_name": crop_info["name"],
        "growth_stage": stage,
        "latest": m,
        "status": status,
        "status_color": status_color,
        "crop_info": crop_info,
        "stage_info": GROWTH_STAGES.get(stage, {}),
        "anomaly_count_24h": anomaly_count,
        "last_watered": dict(last_water) if last_water else None,
        "sensor_health": sensor_health,
        "drying_rate_per_hr": dr,
        "next_water_in_hrs": next_water_hrs,
        "pump_mode": pump_mode,
        "pump_enabled": pump_enabled,
    }


@app.get("/api/history")
async def history(range: str = Query("24h")):
    conn = get_db(); c = conn.cursor()
    hours_map = {"24h": 24, "7d": 168, "30d": 720, "1y": 8760}
    hours = hours_map.get(range, 24)
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = c.execute("SELECT ts,moisture_pct,temp,humidity,n_val,p_val,k_val,ph,ec FROM readings WHERE ts >= ? ORDER BY ts",
                     (since,)).fetchall()
    watering = c.execute("SELECT * FROM watering_log WHERE ts >= ? ORDER BY ts DESC LIMIT 200", (since,)).fetchall()
    anomalies = c.execute("SELECT ts,type,message FROM anomalies WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    conn.close()
    # thin out long ranges so charts stay fast (max ~1000 points)
    step = max(1, len(rows) // 1000)
    rows = rows[::step]
    labels, moisture, temp, humidity = [], [], [], []
    n_arr, p_arr, k_arr, ph_arr, ec_arr = [], [], [], [], []
    for r in rows:
        labels.append(r["ts"])
        moisture.append(r["moisture_pct"])
        temp.append(r["temp"])
        humidity.append(r["humidity"])
        n_arr.append(r["n_val"])
        p_arr.append(r["p_val"])
        k_arr.append(r["k_val"])
        ph_arr.append(r["ph"])
        ec_arr.append(r["ec"])
    return {
        "range": range,
        "labels": labels,
        "moisture": moisture,
        "temp": temp,
        "humidity": humidity,
        "n": n_arr, "p": p_arr, "k": k_arr, "ph": ph_arr, "ec": ec_arr,
        "watering_events": [w["ts"] for w in watering],
        "watering_logs": [dict(w) for w in watering],
        "anomalies": [dict(a) for a in anomalies],
    }


@app.post("/api/water")
async def log_water(entry: WateringEntry):
    conn = get_db(); c = conn.cursor()
    ts = datetime.now().isoformat(timespec="seconds")
    latest = c.execute("SELECT moisture_pct FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    m = latest["moisture_pct"] if latest else None
    c.execute("INSERT INTO watering_log VALUES (NULL,?,?,?,?,?)",
              (ts,"manual",m,None,entry.note))
    conn.commit(); conn.close()
    return {"status":"ok","ts":ts}

@app.get("/api/watering")
async def get_watering():
    conn = get_db(); c = conn.cursor()
    logs = c.execute("SELECT * FROM watering_log ORDER BY ts DESC LIMIT 50").fetchall()
    conn.close()
    return {"logs": [dict(l) for l in logs]}


# AI availability check, cached briefly so opening the chat page stays fast
ai_status_cache = {"ts": 0.0, "data": None}

@app.get("/api/chat/status")
async def chat_status():
    now = time.time()
    if ai_status_cache["data"] and now - ai_status_cache["ts"] < 30:
        return ai_status_cache["data"]
    gemini_key = get_gemini_api_key()
    groq_key = get_ai_api_key()
    data = None
    if not gemini_key and not groq_key:
        data = {"online": False, "configured": False, "provider": None, "model": None,
                "reason": "No AI key in .env (GEMINI_API_KEY or GROQ_API_KEY)"}
    if data is None and gemini_key:
        try:
            r = req_lib.get(
                f"https://generativelanguage.googleapis.com/v1beta/openai/models/{get_gemini_model()}",
                headers={"Authorization": f"Bearer {gemini_key}"},
                timeout=5,
            )
            if r.status_code == 200:
                data = {"online": True, "configured": True, "provider": "gemini",
                        "model": get_gemini_model(), "reason": None}
        except Exception:
            pass  # fall through to Groq
    if data is None and groq_key:
        try:
            r = req_lib.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {groq_key}"},
                timeout=5,
            )
            if r.status_code == 200:
                data = {"online": True, "configured": True, "provider": "groq",
                        "model": get_ai_model(), "reason": None}
            elif r.status_code == 401:
                data = {"online": False, "configured": True, "provider": None, "model": None,
                        "reason": "AI API key is invalid"}
        except Exception:
            pass
    if data is None:
        data = {"online": False, "configured": True, "provider": None, "model": None,
                "reason": "No internet connection to AI service"}
    ai_status_cache["ts"] = now
    ai_status_cache["data"] = data
    return data


@app.get("/api/chat/threads")
async def list_chat_threads(limit: int = Query(30, ge=1, le=100), user_email: Optional[str] = Query(None)):
    email = user_email.strip().lower() if user_email else None
    return {"threads": get_chat_threads(limit, email)}


@app.post("/api/chat/threads")
async def create_chat_thread(payload: ChatThreadCreate):
    conn = get_db(); c = conn.cursor()
    thread_id = str(uuid.uuid4())
    ts = now_ts()
    title = normalize_chat_title(payload.title)
    email = payload.user_email.strip().lower() if payload.user_email else None
    c.execute(
        "INSERT INTO chat_threads (id, title, created_ts, updated_ts, user_email) VALUES (?,?,?,?,?)",
        (thread_id, title, ts, ts, email),
    )
    conn.commit()
    thread = c.execute("SELECT * FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
    conn.close()
    return {"thread": dict(thread), "messages": []}


@app.get("/api/chat/threads/{thread_id}")
async def read_chat_thread(thread_id: str):
    payload = get_chat_thread_payload(thread_id)
    if not payload:
        raise HTTPException(404, "Chat thread not found")
    return payload


@app.post("/api/chat")
async def chat(msg: ChatMessage):
    conn = get_db(); c = conn.cursor()
    crop = get_setting("crop_type", "strawberry")
    plant = get_setting("plant_name", "My Plant")
    stage = get_setting("growth_stage", "vegetative")

    chat_user_email = msg.user_email.strip().lower() if msg.user_email else None
    farm = get_user_farm_context(chat_user_email)
    if farm and farm["primary_plant"]:
        primary = farm["primary_plant"]
        plant = primary.get("name") or plant
        crop = primary.get("crop_type") or crop
        stage = primary.get("growth_stage") or stage
    thread_id = ensure_chat_thread(conn, msg.thread_id, title=normalize_chat_title(msg.message),
                                   user_email=chat_user_email)
    thread_exists = c.execute("SELECT id FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
    if not thread_exists:
        raise HTTPException(500, "Unable to create chat thread")

    existing_count = c.execute("SELECT COUNT(*) AS n FROM chat_messages WHERE thread_id=?", (thread_id,)).fetchone()["n"]
    existing_history_rows = c.execute(
        "SELECT role, content, ts FROM chat_messages WHERE thread_id=? ORDER BY id ASC LIMIT 30",
        (thread_id,),
    ).fetchall()
    existing_history = [dict(r) for r in existing_history_rows]
    save_chat_message(conn, thread_id, "user", msg.message)
    if existing_count == 0:
        touch_chat_thread(conn, thread_id, normalize_chat_title(msg.message))
    else:
        touch_chat_thread(conn, thread_id)

    since = (datetime.now() - timedelta(hours=24)).isoformat()
    rows = c.execute(
        "SELECT ts,moisture_pct,temp,humidity FROM readings WHERE ts >= ? ORDER BY ts DESC LIMIT 48",
        (since,),
    ).fetchall()
    rows = [dict(r) for r in rows]

    plant_ctx = build_plant_context(rows, crop, plant, stage)
    crop_info = plant_ctx["crop_info"]
    last_m = plant_ctx["last_m"]
    last_t = plant_ctx["last_t"]
    last_h = plant_ctx["last_h"]
    dr = plant_ctx["drying_rate"]
    system_prompt = build_chat_system_prompt(plant_ctx["context"])
    if farm:
        registered = ", ".join(
            f"{p.get('name')} ({p.get('crop_type')}, {p.get('growth_stage', 'vegetative')}, quantity {p.get('quantity', 1)})"
            for p in farm["plants"])
        system_prompt += (f"\n\nSigned-in farm profile: {farm['farm_name']}; parcel {farm['parcel_name']}, "
                          f"{farm['parcel_area']} {farm['parcel_unit']}; approximately "
                          f"{farm['approximate_plant_count']} total plants. Registered plants: {registered}. "
                          "Tailor advice to this profile and do not assume a different crop or farm size.")
    knowledge_chunks = retrieve_knowledge(crop, msg.message, k=2)
    if knowledge_chunks:
        system_prompt += "\n\nRelevant crop knowledge (use if helpful):\n" + "\n\n".join(
            ch[:700] for ch in knowledge_chunks)
    if msg.user_name:
        safe_name = " ".join(msg.user_name.split()).strip()[:40]
        if safe_name:
            system_prompt += f"\nThe user's name is {safe_name}. Address them by name when it feels natural."

    # Priority chain: Gemini first, Groq if Gemini fails/exhausted, offline last
    resp = None
    provider = "offline"
    model_used = None
    provider_error = None
    ai_messages = build_groq_messages(existing_history, system_prompt, msg.message)
    if get_gemini_api_key():
        try:
            resp = call_gemini_chat(ai_messages)
            provider = "gemini"
            model_used = get_gemini_model()
        except Exception as e:
            provider_error = f"Gemini failed: {e}"
            print(f"[PlantPulse] Gemini request failed, trying Groq: {e}")
    if resp is None and get_ai_api_key():
        try:
            resp = call_groq_chat(ai_messages)
            provider = "groq"
            model_used = get_ai_model()
        except Exception as e:
            provider_error = f"{provider_error + ' | ' if provider_error else ''}Groq failed: {e}"
            print(f"[PlantPulse] Groq request failed, using offline fallback: {e}")
    if resp is None and not get_gemini_api_key() and not get_ai_api_key():
        provider_error = "No AI key set — add GEMINI_API_KEY or GROQ_API_KEY to .env next to main.py"
        print("[PlantPulse] No AI keys found in environment; using offline fallback")

    # tell the frontend why Gemini was skipped, so users see the switch
    fallback_reason = None
    if provider == "groq" and get_gemini_api_key():
        err = (provider_error or "").lower()
        if "429" in err or "quota" in err or "resource_exhausted" in err or "rate" in err:
            fallback_reason = "Gemini quota exhausted"
        else:
            fallback_reason = "Gemini unavailable"
    if not resp:
        provider = "offline"
        resp = build_offline_chat_reply(msg.message, plant, crop, crop_info, last_m, last_t, last_h, dr)

    save_chat_message(conn, thread_id, "assistant", resp)
    touch_chat_thread(conn, thread_id)
    conn.commit()

    thread = c.execute("SELECT * FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
    messages = c.execute("SELECT role, content, ts FROM chat_messages WHERE thread_id=? ORDER BY id ASC", (thread_id,)).fetchall()
    conn.close()
    return {
        "reply": resp,
        "thread_id": thread_id,
        "thread": dict(thread),
        "messages": [dict(m) for m in messages],
        "context_readings": len(rows),
        "provider": provider,
        "model": model_used,
        "fallback_reason": fallback_reason,
        "provider_error": provider_error,
        "last_sensor": {
            "moisture": last_m,
            "temp": last_t,
            "humidity": last_h,
            "drying_rate": dr,
        },
    }


@app.get("/api/settings")
async def get_settings():
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM settings").fetchall()
    conn.close()
    hidden = {"groq_key", "groq_model", "gemini_key"}
    return {r["key"]: r["value"] for r in rows if r["key"] not in hidden}

@app.post("/api/settings")
async def save_settings(s: SettingsUpdate):
    data = s.dict(exclude_none=True)
    data.pop("groq_key", None)
    data.pop("groq_model", None)
    data.pop("gemini_key", None)
    for k, v in data.items():
        set_setting(k, v)
    return {"status":"ok","updated":list(data.keys())}


@app.get("/api/export/csv")
async def export_csv(range: str = Query("24h")):
    hours_map = {"24h":24,"7d":168,"30d":720}
    hours = hours_map.get(range, 24)
    since = (datetime.now()-timedelta(hours=hours)).isoformat()
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp","raw","moisture_pct","scale","temp","humidity","light","source","note"])
    for r in rows:
        writer.writerow([r["ts"],r["raw"],r["moisture_pct"],r["scale"],r["temp"],r["humidity"],r["light"],r["source"],r["note"]])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.read().encode()), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=plantpulse_{range}.csv"})


@app.get("/api/anomalies")
async def get_anomalies(range: str = Query("24h")):
    hours_map = {"24h":24,"7d":168,"30d":720}
    hours = hours_map.get(range, 24)
    since = (datetime.now()-timedelta(hours=hours)).isoformat()
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM anomalies WHERE ts >= ? ORDER BY ts DESC LIMIT 50", (since,)).fetchall()
    conn.close()
    return {"anomalies":[dict(r) for r in rows]}


@app.get("/api/crops")
async def get_crops():
    return {"crops": {k: v["name"] for k,v in CROP_DB.items()}}

@app.get("/api/crop/{crop_id}")
async def get_crop(crop_id: str):
    if crop_id not in CROP_DB: raise HTTPException(404,"Crop not found")
    return CROP_DB[crop_id]


# Pump API (UI ready, hardware optional)
pump_state = {"on": False, "start_ts": None, "mode": "manual"}
spray_state = {"on": False, "start_ts": None, "method": None}

@app.post("/api/spray/on")
async def spray_on():
    if pump_state["on"]:
        raise HTTPException(409, "Stop irrigation before starting fertilizer spray")
    spray_state.update(on=True, start_ts=datetime.now().isoformat(),
                       method=get_setting("pump_fertilizer_method", "fertigation"))
    return {"status": "spray_on", **spray_state}

@app.post("/api/spray/off")
async def spray_off():
    spray_state.update(on=False, start_ts=None)
    return {"status": "spray_off", **spray_state}

@app.get("/api/spray/status")
async def spray_status():
    return spray_state

@app.post("/api/pump/on")
async def pump_on():
    if spray_state["on"]:
        raise HTTPException(409, "Stop fertilizer spray before starting irrigation")
    pump_state["on"] = True
    pump_state["start_ts"] = datetime.now().isoformat()
    return {"status":"pump_on","ts":pump_state["start_ts"]}

@app.post("/api/pump/off")
async def pump_off():
    if pump_state["on"] and pump_state["start_ts"]:
        dur = int((datetime.now()-datetime.fromisoformat(pump_state["start_ts"])).total_seconds())
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO pump_log VALUES (NULL,?,?,?,?,?,?,?)",
                  (pump_state["start_ts"],datetime.now().isoformat(),
                   pump_state["mode"],"manual_stop",None,None,dur))
        conn.commit(); conn.close()
    pump_state["on"] = False
    return {"status":"pump_off"}

@app.post("/api/pump/mode")
async def set_pump_mode(data: PumpModeUpdate):
    pump_state["mode"] = data.mode
    set_setting("pump_mode", data.mode)
    return {"status":"ok","mode":data.mode}

@app.get("/api/pump/status")
async def pump_status():
    dur = None
    if pump_state["on"] and pump_state["start_ts"]:
        dur = int((datetime.now()-datetime.fromisoformat(pump_state["start_ts"])).total_seconds())
    return {**pump_state, "runtime_sec": dur}

@app.get("/api/pump/log")
async def pump_log():
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM pump_log ORDER BY start_ts DESC LIMIT 20").fetchall()
    conn.close()
    return {"logs":[dict(r) for r in rows]}



# ─────────────────────────────────────────────
# DEVICE TRACKING
# ─────────────────────────────────────────────
def init_devices():
    conn = get_db(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        name TEXT,
        plant_name TEXT,
        last_seen TEXT,
        last_ip TEXT,
        rssi INTEGER,
        readings_today INTEGER DEFAULT 0,
        posts_ok INTEGER DEFAULT 0,
        posts_fail INTEGER DEFAULT 0
    )""")
    conn.commit(); conn.close()

init_devices()

def update_device(device_id, ip, rssi=None):
    conn = get_db(); c = conn.cursor()
    ts = datetime.now().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    existing = c.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if existing:
        c.execute("""UPDATE devices SET last_seen=?, last_ip=?, rssi=?,
                     readings_today=readings_today+1, posts_ok=posts_ok+1
                     WHERE device_id=?""", (ts, ip, rssi, device_id))
    else:
        c.execute("""INSERT INTO devices VALUES (?,?,?,?,?,?,1,1,0)""",
                  (device_id, f"ESP8266-{device_id[:6]}", "Unassigned", ts, ip, rssi))
    conn.commit(); conn.close()


class DeviceUpdate(BaseModel):
    device_id: str
    name: Optional[str] = None
    plant_name: Optional[str] = None


@app.get("/api/devices")
async def get_devices():
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    conn.close()
    now = datetime.now()
    result = []
    for r in rows:
        d = dict(r)
        try:
            last = datetime.fromisoformat(d["last_seen"])
            diff_secs = (now - last).total_seconds()
            d["online"] = diff_secs < 120       # online if seen in last 2 min
            d["last_seen_ago"] = _ago(diff_secs)
            d["gap_warning"] = diff_secs > 300  # warn if >5 min gap
        except:
            d["online"] = False
            d["last_seen_ago"] = "Unknown"
            d["gap_warning"] = True
        result.append(d)
    return {"devices": result}


@app.post("/api/devices/update")
async def update_device_info(data: DeviceUpdate):
    conn = get_db(); c = conn.cursor()
    if data.name:
        c.execute("UPDATE devices SET name=? WHERE device_id=?", (data.name, data.device_id))
    if data.plant_name:
        c.execute("UPDATE devices SET plant_name=? WHERE device_id=?", (data.plant_name, data.device_id))
    conn.commit(); conn.close()
    return {"status": "ok"}


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str):
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
    conn.commit(); conn.close()
    return {"status": "deleted"}


@app.get("/api/server/info")
async def server_info(request_obj: None = None):
    import socket
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        local_ip = _s.getsockname()[0]
        _s.close()
    except:
        local_ip = "127.0.0.1"
    return {
        "server_ip": local_ip,
        "port": 8000,
        "dashboard_url": f"http://{local_ip}:8000",
        "ingest_url": f"http://{local_ip}:8000/api/ingest",
        "hostname": socket.gethostname(),
    }


@app.get("/api/ping")
async def ping():
    return {"status": "ok", "ts": datetime.now().isoformat()}


def _ago(secs):
    if secs < 60: return f"{int(secs)}s ago"
    if secs < 3600: return f"{int(secs//60)}m ago"
    if secs < 86400: return f"{int(secs//3600)}h ago"
    return f"{int(secs//86400)}d ago"


@app.get("/api/server/ip")
async def get_server_ip():
    import socket
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        ip = _s.getsockname()[0]
        _s.close()
    except:
        ip = "127.0.0.1"
    return {"ip": ip, "port": 8000}

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn, threading, webbrowser, urllib.request

    def _open_browser_when_ready():
        # Wait until the server actually answers, then open the dashboard.
        # Runs only in the launcher process, so code auto-reloads won't reopen tabs.
        url = "http://localhost:8000"
        for _ in range(240):
            try:
                urllib.request.urlopen(url + "/api/ping", timeout=1)
                print("[PlantPulse] Server is ready - opening browser...")
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    # Auto-reload restarts the server on .py changes, which kills in-flight
    # chat requests. Keep it off for normal use; set PLANTPULSE_DEV=1 to develop.
    dev_mode = os.getenv("PLANTPULSE_DEV", "").strip() == "1"
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=dev_mode,
                    app_dir=BASE_DIR, reload_dirs=[BASE_DIR] if dev_mode else None)
    except KeyboardInterrupt:
        pass
    print("\n[PlantPulse] Stopped. Run start.bat to launch again. Bye!")
