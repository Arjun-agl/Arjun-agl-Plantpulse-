#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPClient.h>
#include <EEPROM.h>
#include <DHT.h>
#include <ArduinoJson.h>

const char* DEVICE_ID   = "esp8266-01";
const int   SERVER_PORT = 8000;
#define DHT_PIN       D4
#define DHT_TYPE      DHT11
#define MOISTURE_PIN  A0
const int AIR_VALUE   = 900;
const int WATER_VALUE = 350;

#define EEPROM_SIZE  200
#define SSID_ADDR      0
#define PASS_ADDR     50
#define IP_ADDR      100

DHT dht(DHT_PIN, DHT_TYPE);
WiFiClient wifiClient;
ESP8266WebServer configServer(80);
String savedSSID, savedPASS, savedIP;
int failCount = 0;

void writeStr(int addr, String s) {
  for (int i = 0; i < 50; i++)
    EEPROM.write(addr + i, i < (int)s.length() ? s[i] : 0);
}
String readStr(int addr) {
  String s = "";
  for (int i = 0; i < 50; i++) {
    char c = EEPROM.read(addr + i);
    if (c == 0) break;
    s += c;
  }
  return s;
}

void startConfigPortal() {
  WiFi.softAP("PlantPulse-Setup", "plantpulse123");
  Serial.println("=== CONFIG MODE ===");
  Serial.println("Hotspot: PlantPulse-Setup | pass: plantpulse123");
  Serial.println("Open browser -> http://192.168.4.1");

  // ROOT PAGE — simple form at 192.168.4.1
  configServer.on("/", HTTP_GET, []() {
    String html = "<!DOCTYPE html><html><head>"
      "<meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<title>PlantPulse Setup</title>"
      "<style>"
      "body{font-family:sans-serif;background:#0a1a0a;color:#e0f2e9;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
      ".box{background:#112211;border:1px solid #1cff95;border-radius:16px;padding:32px;width:90%;max-width:400px}"
      "h2{color:#1cff95;margin:0 0 6px 0}p{color:#aaa;margin:0 0 24px 0;font-size:14px}"
      "label{font-size:13px;color:#aaa;display:block;margin-bottom:4px}"
      "input{width:100%;padding:12px;border-radius:8px;border:1px solid #1cff95;background:#0a1a0a;color:#e0f2e9;font-size:15px;box-sizing:border-box;margin-bottom:16px}"
      "button{width:100%;padding:14px;background:#1cff95;color:#000;font-weight:800;font-size:16px;border:none;border-radius:10px;cursor:pointer}"
      "#msg{margin-top:16px;text-align:center;font-size:14px}"
      "</style></head><body>"
      "<div class='box'>"
      "<h2>🌱 PlantPulse Setup</h2>"
      "<p>Enter your home Wi-Fi details below</p>"
      "<form method='POST' action='/save'>"
      "<label>Wi-Fi Name (SSID)</label>"
      "<input name='ssid' placeholder='Your Wi-Fi name' required>"
      "<label>Wi-Fi Password</label>"
      "<input name='pass' type='password' placeholder='Your Wi-Fi password'>"
      "<label>Server IP (your PC's IP)</label>"
      "<input name='ip' placeholder='e.g. 192.168.1.14' required>"
      "<button type='submit'>💾 Save & Connect</button>"
      "</form>"
      "<div id='msg'></div>"
      "</div></body></html>";
    configServer.send(200, "text/html", html);
  });

  // SAVE ROUTE — handles form POST from browser
  configServer.on("/save", HTTP_POST, []() {
    if (!configServer.hasArg("ssid") || !configServer.hasArg("ip")) {
      configServer.send(400, "text/html", "<h2>Error: missing SSID or IP</h2>");
      return;
    }
    String ssid = configServer.arg("ssid");
    String pass = configServer.arg("pass");
    String ip   = configServer.arg("ip");
    Serial.println("Saving: SSID=[" + ssid + "] IP=[" + ip + "]");
    EEPROM.begin(EEPROM_SIZE);
    writeStr(SSID_ADDR, ssid);
    writeStr(PASS_ADDR, pass);
    writeStr(IP_ADDR,   ip);
    EEPROM.commit();
    String doneHtml = "<!DOCTYPE html><html><head>"
      "<meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<style>body{font-family:sans-serif;background:#0a1a0a;color:#1cff95;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}</style>"
      "</head><body><h2>✅ Saved!</h2><p style='color:#aaa'>ESP is rebooting and connecting to <b style='color:#1cff95'>" + ssid + "</b><br><br>Reconnect your device to your normal Wi-Fi.<br>Then open the PlantPulse dashboard.</p></body></html>";
    configServer.send(200, "text/html", doneHtml);
    Serial.println("Saved! Rebooting...");
    delay(500);
    ESP.restart();
  });

  // Also keep /push-wifi for future dashboard pushes
  configServer.on("/push-wifi", HTTP_POST, []() {
    configServer.sendHeader("Access-Control-Allow-Origin", "*");
    if (!configServer.hasArg("ssid") || !configServer.hasArg("ip")) {
      configServer.send(400, "application/json", "{\"error\":\"missing fields\"}");
      return;
    }
    String ssid = configServer.arg("ssid");
    String pass = configServer.arg("pass");
    String ip   = configServer.arg("ip");
    EEPROM.begin(EEPROM_SIZE);
    writeStr(SSID_ADDR, ssid);
    writeStr(PASS_ADDR, pass);
    writeStr(IP_ADDR,   ip);
    EEPROM.commit();
    configServer.send(200, "application/json", "{\"status\":\"saved\",\"rebooting\":true}");
    delay(300);
    ESP.restart();
  });

  configServer.on("/push-wifi", HTTP_OPTIONS, []() {
    configServer.sendHeader("Access-Control-Allow-Origin", "*");
    configServer.sendHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    configServer.sendHeader("Access-Control-Allow-Headers", "Content-Type");
    configServer.send(204);
  });

  configServer.begin();
  Serial.println("Config server ready. Open http://192.168.4.1");
  while (true) configServer.handleClient();
}

void connectWiFi() {
  WiFi.begin(savedSSID.c_str(), savedPASS.c_str());
  Serial.printf("Connecting to [%s]", savedSSID.c_str());
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500); Serial.print("."); tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
    Serial.println("Server IP: " + savedIP);
  } else {
    Serial.println("\nWi-Fi failed — back to config portal");
    startConfigPortal();
  }
}

void setup() {
  Serial.begin(115200); delay(100);
  Serial.println("\n=== PlantPulse ESP8266 ===");
  EEPROM.begin(EEPROM_SIZE);
  savedSSID = readStr(SSID_ADDR);
  savedPASS = readStr(PASS_ADDR);
  savedIP   = readStr(IP_ADDR);
  Serial.println("Saved SSID: [" + savedSSID + "]");
  Serial.println("Saved IP:   [" + savedIP + "]");
  if (savedSSID.length() == 0 || savedIP.length() == 0) {
    startConfigPortal();
  } else {
    connectWiFi();
  }
  dht.begin();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost — reconnecting...");
    connectWiFi(); delay(5000); return;
  }
  int raw    = analogRead(MOISTURE_PIN);
  float pct  = constrain((float)(AIR_VALUE - raw) / (AIR_VALUE - WATER_VALUE) * 100.0, 0, 100);
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();
  int rssi   = WiFi.RSSI();
  Serial.printf("[%s] Raw:%d Pct:%.1f Temp:%.1f Hum:%.1f RSSI:%d\n",
                DEVICE_ID, raw, pct, temp, hum, rssi);

  StaticJsonDocument<256> doc;
  doc["raw"]          = raw;
  doc["moisture_pct"] = round(pct * 10) / 10.0;
  doc["scale"]        = round((pct / 10.0) * 10) / 10.0;
  if (!isnan(temp)) doc["temp"]     = temp;
  if (!isnan(hum))  doc["humidity"] = hum;
  doc["source"]    = DEVICE_ID;
  doc["device_id"] = DEVICE_ID;
  doc["rssi"]      = rssi;

  String payload; serializeJson(doc, payload);
  HTTPClient http;
  String url = "http://" + savedIP + ":" + String(SERVER_PORT) + "/api/ingest";
  http.begin(wifiClient, url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(8000);
  int code = http.POST(payload);
  if (code == 200) { Serial.println("POST OK"); failCount = 0; }
  else {
    Serial.printf("POST FAILED: %d\n", code); failCount++;
    if (failCount >= 5) { WiFi.disconnect(); delay(2000); connectWiFi(); failCount = 0; }
  }
  http.end();
  delay(60000);
}
