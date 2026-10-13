"""
Seed PlantPulse with 1 year of realistic demo data.

Uses REAL historical weather from Open-Meteo (free, no key) for the location
set in Settings, then simulates soil moisture physically on top of it:
  - soil dries faster on hot/dry days (driven by real evapotranspiration)
  - rain soaks the pot (outdoor mode)
  - the gardener waters in the morning/evening when soil crosses the dry line

Run:  venv\\Scripts\\python.exe seed_demo_data.py
Re-running clears previously seeded data first (source='seed').
"""
import os, sqlite3, random, math
from datetime import datetime, timedelta
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "plantpulse.db")

# strawberry profile (matches CROP_DB in main.py)
DRY, WET = 55, 85
AIR, WATER = 900, 350  # raw sensor calibration

random.seed(42)


def get_setting(c, key, default):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def fetch_weather(lat, lon):
    """Real hourly weather: archive API for the year + forecast API for recent days."""
    today = datetime.now().date()
    start = today - timedelta(days=365)
    merged = {}  # time -> (temp, hum, precip, et0)

    print(f"Fetching 1 year of real weather for {lat},{lon} from Open-Meteo...")
    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat, "longitude": lon,
            # the archive API rejects today's date; the forecast call below covers recent days
            "start_date": start.isoformat(), "end_date": (today - timedelta(days=1)).isoformat(),
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,et0_fao_evapotranspiration",
            "timezone": "auto",
        }, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    for i, ts in enumerate(h["time"]):
        merged[ts] = (h["temperature_2m"][i], h["relative_humidity_2m"][i],
                      h["precipitation"][i], h["et0_fao_evapotranspiration"][i])

    # archive lags a few days behind; cover the gap with the forecast API's past days
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,et0_fao_evapotranspiration",
            "past_days": 14, "forecast_days": 1, "timezone": "auto",
        }, timeout=30)
    r.raise_for_status()
    h = r.json()["hourly"]
    for i, ts in enumerate(h["time"]):
        if h["temperature_2m"][i] is not None:
            merged[ts] = (h["temperature_2m"][i], h["relative_humidity_2m"][i],
                          h["precipitation"][i], h["et0_fao_evapotranspiration"][i])

    now_key = datetime.now().strftime("%Y-%m-%dT%H:00")
    times = sorted(t for t in merged if t <= now_key)
    print(f"Got {len(times)} hourly weather points ({times[0]} .. {times[-1]})")
    return times, merged


def simulate(times, weather):
    """Walk hour by hour; return readings, waterings, anomalies."""
    readings, waterings, anomalies = [], [], []
    m = 72.0
    prev_temp = None
    last_water_day = None
    # soil nutrient state (NPK mg/kg, pH, EC mS/cm) — the gardener fertilizes
    # roughly monthly; rain leaches nutrients; EC tracks total dissolved salts
    n_v, p_v, k_v, ph_v = 45.0, 24.0, 160.0, 6.2
    hours_since_fert = 0.0

    for ts in times:
        temp, hum, precip, et0 = weather[ts]
        if temp is None:
            continue
        hour = int(ts[11:13])

        # drying: baseline + evapotranspiration-driven (hot dry afternoons dry fastest)
        m -= 0.06 + (et0 or 0) * 1.15
        # rain soaks the pot (outdoor plant)
        if precip and precip > 0:
            m = min(88.0, m + precip * 3.0)

        watered = False
        # gardener habit: water in morning (6-9) or evening (17-19) when below dry line
        if m < DRY and (6 <= hour <= 9 or 17 <= hour <= 19):
            watered = True
        # emergency: badly dry, water whenever noticed
        elif m < DRY - 7:
            watered = True

        if watered:
            before = round(m, 1)
            m = min(86.0, m + random.uniform(22, 29))
            method = "manual" if random.random() < 0.8 else "auto_detected"
            waterings.append((ts + ":00", method, before, round(m, 1),
                              "Routine watering" if before > DRY - 7 else "Soil got very dry!"))
            if before < DRY - 5:
                anomalies.append((ts + ":00", "dry_soil", "error",
                                  f"Plant needed water! Moisture: {before}%", before))
            last_water_day = ts[:10]

        m = max(3.0, min(90.0, m))

        # ── nutrient dynamics ──
        hours_since_fert += 1
        uptake = 1.0 + (0.5 if 6 <= hour <= 18 else 0.0)          # plants feed by day
        n_v -= 0.010 * uptake
        p_v -= 0.0025 * uptake
        k_v -= 0.030 * uptake
        if precip and precip > 0:                                  # rain leaches nutrients
            leach = min(precip, 8.0)
            n_v -= 0.12 * leach
            k_v -= 0.30 * leach
            ph_v -= 0.004 * leach                                  # rain is slightly acidic
        # monthly-ish fertilizing habit (when N or K run low and it isn't pouring)
        if (n_v < 28 or k_v < 110) and hours_since_fert > 340 and (not precip or precip < 1):
            n_v = min(62.0, n_v + random.uniform(20, 28))
            p_v = min(38.0, p_v + random.uniform(6, 10))
            k_v = min(240.0, k_v + random.uniform(60, 90))
            ph_v -= 0.08                                           # fertilizer salts dip pH briefly
            hours_since_fert = 0
        ph_v += (6.3 - ph_v) * 0.002                               # soil buffers back toward ~6.3
        n_v = max(8.0, n_v); p_v = max(6.0, p_v); k_v = max(50.0, k_v)
        ph_v = max(5.2, min(7.4, ph_v))
        ec_v = round(0.25 + n_v * 0.012 + k_v * 0.005 + p_v * 0.008 + random.uniform(-0.05, 0.05), 2)

        noisy = round(max(0.0, min(100.0, m + random.uniform(-0.4, 0.4))), 1)
        raw = int(AIR - noisy / 100 * (AIR - WATER))
        hum_v = round(min(100, max(5, hum + random.uniform(-1.5, 1.5))), 1) if hum is not None else None
        temp_v = round(temp + random.uniform(-0.3, 0.3), 1)
        readings.append((ts + ":00", raw, noisy, round(noisy / 10, 1), temp_v, hum_v, None, "seed", None,
                         round(n_v + random.uniform(-0.8, 0.8), 1),
                         round(p_v + random.uniform(-0.4, 0.4), 1),
                         round(k_v + random.uniform(-2.0, 2.0), 1),
                         round(ph_v + random.uniform(-0.03, 0.03), 2),
                         ec_v))

        if prev_temp is not None and temp_v - prev_temp >= 5:
            anomalies.append((ts + ":00", "temp_spike", "warning",
                              f"Temperature spiked by {round(temp_v - prev_temp, 1)}°C", temp_v))
        prev_temp = temp_v

    # keep anomalies list believable, not overwhelming
    random.shuffle(anomalies)
    anomalies = sorted(anomalies[:60])
    return readings, waterings, anomalies


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    lat = get_setting(c, "latitude", "19.0760")
    lon = get_setting(c, "longitude", "72.8777")

    times, weather = fetch_weather(lat, lon)
    readings, waterings, anomalies = simulate(times, weather)

    print("Clearing previously seeded demo data...")
    c.execute("DELETE FROM readings WHERE source='seed'")
    c.execute("DELETE FROM readings")           # also drop old test readings
    c.execute("DELETE FROM watering_log")
    c.execute("DELETE FROM anomalies")

    print(f"Inserting {len(readings)} readings, {len(waterings)} waterings, {len(anomalies)} anomalies...")
    c.executemany("INSERT INTO readings (ts,raw,moisture_pct,scale,temp,humidity,light,source,note,n_val,p_val,k_val,ph,ec) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", readings)
    c.executemany("INSERT INTO watering_log (ts,method,moisture_before,moisture_after,note) "
                  "VALUES (?,?,?,?,?)", waterings)
    c.executemany("INSERT INTO anomalies (ts,type,severity,message,value) VALUES (?,?,?,?,?)", anomalies)
    conn.commit()

    last = c.execute("SELECT ts, moisture_pct, temp, humidity FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    total = c.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    conn.close()
    print(f"Done! {total} readings in DB. Latest: {last[0]} — moisture {last[1]}%, temp {last[2]}°C, humidity {last[3]}%")


if __name__ == "__main__":
    main()
