/*
 * PCS_ESP32CAM.ino
 * ParkingCamSecurity — Firmware ESP32-CAM v2 (AI Thinker OV2640/OV3660)
 *
 * Architecture 3 tâches FreeRTOS :
 *   1. camTask     — capture continue, non-bloquante
 *   2. streamTask  — envoi HTTP POST au serveur (limité en FPS)
 *   3. sdTask      — écriture SD en lot, non-bloquante
 *
 * Optimisations anti-FB-overflow :
 *   - fb_count = 2 (pas plus : chaque frame occupe ~40KB en PSRAM)
 *   - Files d'attente entre tâches (pas de flush synchrone)
 *   - PSRAM pour les buffers de frame
 *   - Intervalle de capture + élégant (pas de busy-wait)
 */

#include "esp_camera.h"
#include "WiFi.h"
#include "HTTPClient.h"
#include "WebServer.h"
#include "Preferences.h"
#include "SD_MMC.h"
#include "time.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"


// ═══════════════════════════════════════════════
// PINS — AI Thinker ESP32-CAM
// ═══════════════════════════════════════════════
#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM    0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM      5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22

#define CONFIG_BTN_PIN  13

RTC_DATA_ATTR int  rtcResetCount = 0;
RTC_DATA_ATTR uint32_t rtcResetMs = 0;

// ═══════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════
struct Config {
  char wifi_ssid[64]   = "";
  char wifi_pass[64]   = "";
  char camera_id[33]   = "esp32cam-001";
  char server_url[128] = "https://web-production-10852.up.railway.app";
  int  rotation        = 0;
  int  quality         = 18;
  int  fps_limit      = 5;
};

Config cfg;
Preferences prefs;
WebServer webServer(80);

bool configMode = false;
bool sdReady    = false;
bool ntpReady   = false;

// ═══════════════════════════════════════════════
// BUFFERS PRÉ-ALLOUÉS — zéro malloc par frame
// ═══════════════════════════════════════════════
#define FRAME_MAX   60000   // 60 KB : VGA quality 12 ≈ 15-35 KB
#define N_SBUF      3       // slots stream (ping-pong-pong)
#define N_SDBUF     6       // slots SD pool

// ── Stream : dernière frame disponible ───────────────────────────
static uint8_t          *sBuf[N_SBUF]  = {};
static size_t            sLen[N_SBUF]  = {};
static volatile int      sCapIdx       = 0;    // camTask écrit ici
static volatile int      sLatestIdx    = -1;   // dernier slot complet
static SemaphoreHandle_t sMux          = NULL; // protège sLatestIdx + copie

// Buffer HTTP pour streamTask (alloué une fois dans la tâche)
static uint8_t *httpBuf = NULL;

// ── SD pool : pré-alloué, jamais free/malloc ──────────────────────
struct FrameItem { uint8_t *data; size_t len; };
static uint8_t      *sdPool[N_SDBUF] = {};
static QueueHandle_t qSdFree  = NULL;   // slots libres
static QueueHandle_t qSdReady = NULL;   // slots remplis

// ── Fichier SD en cours ───────────────────────────────────────────
static File     recFile;
static String   currentHourKey = "";
static uint32_t recFrameCount  = 0;
static unsigned long lastSdFlush = 0;

// ═══════════════════════════════════════════════
// HTML — Config
// ═══════════════════════════════════════════════
const char CONFIG_HTML[] PROGMEM = R"HTML(
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PCS — Configuration</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f1f5f9;min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:20px}
.card{background:#fff;border-radius:16px;padding:28px;width:100%;max-width:460px;box-shadow:0 4px 24px rgba(0,0,0,.08)}
.brand{font-size:18px;font-weight:800;color:#1e40af;margin-bottom:2px}
.sub{font-size:13px;color:#64748b;margin-bottom:24px}
h2{font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;margin:20px 0 12px;padding-bottom:6px;border-bottom:1px solid #f1f5f9}
label{display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:4px}
input,select{width:100%;padding:10px 14px;border:1.5px solid #e2e8f0;border-radius:10px;font-size:14px;background:#f8fafc;margin-bottom:4px;outline:none;transition:border .15s}
input:focus,select:focus{border-color:#3b82f6;background:#fff}
.hint{font-size:11px;color:#94a3b8;margin-bottom:14px}
.row{display:flex;gap:10px}.row>div{flex:1}
button{width:100%;background:#2563eb;color:#fff;border:none;padding:13px;border-radius:12px;font-size:15px;font-weight:700;cursor:pointer;margin-top:10px;transition:background .15s}
button:hover{background:#1d4ed8}
.saved{background:#f0fdf4;border:1px solid #bbf7d0;color:#16a34a;border-radius:8px;padding:10px 14px;font-size:13px;margin-bottom:16px;display:none}
.link{display:block;text-align:center;margin-top:16px;color:#2563eb;font-size:13px;font-weight:600;text-decoration:none}
.ip{font-size:12px;color:#94a3b8;text-align:center;margin-top:8px}
</style>
</head>
<body>
<div class="card">
  <div class="brand">ParkingCamSecurity</div>
  <div class="sub">Configuration ESP32-CAM</div>
  <div class="saved" id="ok">✓ Sauvegardé — redémarrage en cours...</div>
  <form method="POST" action="/save" onsubmit="document.getElementById('ok').style.display='block'">
    <h2>Réseau WiFi</h2>
    <label>SSID</label>
    <input name="ssid" value="%SSID%" placeholder="MonReseau" required autocomplete="off" autocapitalize="none">
    <div class="hint">Réseaux cachés supportés — entrez le nom exact</div>
    <label>Mot de passe</label>
    <input name="pass" type="password" value="%PASS%" placeholder="••••••••" autocomplete="new-password">
    <div class="hint">Laisser vide si réseau ouvert</div>

    <h2>Identité caméra</h2>
    <label>Camera ID (= API Key)</label>
    <input name="cam_id" value="%CAM_ID%" placeholder="parking-entree-01" maxlength="32" required pattern="[a-zA-Z0-9_-]+" autocapitalize="none">
    <div class="hint">Correspond à la clé API du dashboard PCS</div>
    <label>URL du serveur PCS</label>
    <input name="server" value="%SERVER%" placeholder="https://..." required>

    <h2>Image</h2>
    <label>Rotation / Orientation</label>
    <select name="rotation">
      <option value="0" %R0%>Normal (0°)</option>
      <option value="1" %R1%>180° retourné</option>
      <option value="2" %R2%>Miroir horizontal</option>
      <option value="3" %R3%>Miroir vertical</option>
    </select>
    <div class="row">
      <div>
        <label>Qualité JPEG</label>
        <select name="quality">
          <option value="6"  %Q6% >Ultra (6)</option>
          <option value="8"  %Q8% >Haute (8)</option>
          <option value="12" %Q12%>Normale (12)</option>
          <option value="18" %Q18% selected>Économe (18)</option>
          <option value="20" %Q20%>Très économe (20)</option>
          <option value="25" %Q25%>Minimum (25)</option>
        </select>
      </div>
      <div>
        <label>FPS serveur</label>
        <select name="fps">
          <option value="1"  %F1% >1 fps</option>
          <option value="2"  %F2% >2 fps</option>
          <option value="5"  %F5% selected>5 fps</option>
          <option value="10" %F10%>10 fps</option>
        </select>
      </div>
    </div>
    <button type="submit">Sauvegarder et redémarrer</button>
  </form>
  <a class="link" href="/recordings">📼 Enregistrements SD</a>
  <div class="ip">IP : %IP% | Mem : %MEM% KB | PSRAM : %PSRAM% KB</div>
</div>
</body></html>
)HTML";

const char REC_HTML_HEAD[] PROGMEM = R"HTML(
<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PCS — Enregistrements</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f1f5f9;padding:20px}
h1{font-size:20px;font-weight:800;color:#1e40af;margin-bottom:4px}
.sub{color:#64748b;font-size:13px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.hour{font-size:16px;font-weight:700;color:#1e293b}
.meta{font-size:12px;color:#94a3b8;margin:4px 0 12px}
.btns{display:flex;gap:8px}
.btn{flex:1;padding:9px;border-radius:8px;font-size:13px;font-weight:600;text-align:center;text-decoration:none;border:none;cursor:pointer}
.play{background:#eff6ff;color:#2563eb}
.dl{background:#f0fdf4;color:#16a34a}
.back{display:inline-block;margin-bottom:20px;color:#2563eb;font-size:13px;font-weight:600;text-decoration:none}
.empty{text-align:center;color:#94a3b8;padding:60px 20px;grid-column:1/-1}
</style></head><body>
<a class="back" href="/">← Configuration</a>
<h1>Enregistrements SD</h1>
<div class="sub">Dernières 24h — Play pour visionner, Télécharger pour extraire</div>
<div class="grid">
)HTML";

const char REC_HTML_FOOT[] PROGMEM = R"HTML(
</div></body></html>
)HTML";

// ═══════════════════════════════════════════════
// CONFIG (Preferences)
// ═══════════════════════════════════════════════
void loadConfig() {
  prefs.begin("pcs", true);
  strlcpy(cfg.wifi_ssid,  prefs.getString("ssid",    "").c_str(),       sizeof(cfg.wifi_ssid));
  strlcpy(cfg.wifi_pass,  prefs.getString("pass",    "").c_str(),       sizeof(cfg.wifi_pass));
  strlcpy(cfg.camera_id,  prefs.getString("cam_id",  "esp32cam-001").c_str(), sizeof(cfg.camera_id));
  strlcpy(cfg.server_url, prefs.getString("server",  "https://web-production-10852.up.railway.app").c_str(), sizeof(cfg.server_url));
  cfg.rotation  = prefs.getInt("rotation", 0);
  cfg.quality   = prefs.getInt("quality", 18);
  cfg.fps_limit = prefs.getInt("fps", 5);
  prefs.end();
}

void saveConfig() {
  prefs.begin("pcs", false);
  prefs.putString("ssid",   cfg.wifi_ssid);
  prefs.putString("pass",   cfg.wifi_pass);
  prefs.putString("cam_id", cfg.camera_id);
  prefs.putString("server", cfg.server_url);
  prefs.putInt("rotation",  cfg.rotation);
  prefs.putInt("quality",   cfg.quality);
  prefs.putInt("fps",       cfg.fps_limit);
  prefs.end();
}

// ═══════════════════════════════════════════════
// CAMERA INIT
// ═══════════════════════════════════════════════
bool initCamera() {
  camera_config_t config;
  memset(&config, 0, sizeof(config));
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 8000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA;
  config.jpeg_quality = cfg.quality;
  config.fb_count     = 2;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] Init failed: 0x%x\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (!s) return false;

  Serial.printf("[CAM] Sensor PID: 0x%02x\n", s->id.PID);

  if (s->id.PID == 0x3660) {
    Serial.println("[CAM] OV3660 — vflip natif");
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, 0);
  } else if (s->id.PID == 0x2640) {
    Serial.println("[CAM] OV2640 détecté");
  }

  switch (cfg.rotation) {
    case 0: s->set_vflip(s, 0); s->set_hmirror(s, 0); break;
    case 1: s->set_vflip(s, 1); s->set_hmirror(s, 1); break;
    case 2: s->set_vflip(s, 0); s->set_hmirror(s, 1); break;
    case 3: s->set_vflip(s, 1); s->set_hmirror(s, 0); break;
  }

  Serial.printf("[CAM] Init OK — SVGA q=%d fb=%d\n", cfg.quality, config.fb_count);
  return true;
}

// ═══════════════════════════════════════════════
// SD INIT
// ═══════════════════════════════════════════════
bool initSD() {
  if (!SD_MMC.begin("/sdcard", true)) {
    Serial.println("[SD] Mount failed");
    return false;
  }
  if (SD_MMC.cardType() == CARD_NONE) {
    Serial.println("[SD] No card");
    return false;
  }
  if (!SD_MMC.exists("/rec")) SD_MMC.mkdir("/rec");
  Serial.printf("[SD] OK — %llu MB\n", SD_MMC.cardSize() / (1024 * 1024));
  return true;
}

// ═══════════════════════════════════════════════
// NTP
// ═══════════════════════════════════════════════
void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  struct tm t;
  int tries = 0;
  while (!getLocalTime(&t) && tries++ < 20) delay(500);
  ntpReady = (tries < 20);
  Serial.printf("[NTP] %s\n", ntpReady ? "OK" : "FAIL");
}

// ═══════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════
String getHourKey() {
  struct tm t;
  if (!getLocalTime(&t)) return "0000-00-00_00";
  char buf[20];
  strftime(buf, sizeof(buf), "%Y-%m-%d_%H", &t);
  return String(buf);
}

String getFilePath(const String &hourKey) {
  return "/rec/" + hourKey + ".pcs";
}

void purgeOldRecordings() {
  File dir = SD_MMC.open("/rec");
  if (!dir || !dir.isDirectory()) return;

  static struct Entry { char name[64]; time_t mtime; } files[64];
  int count = 0;

  File f = dir.openNextFile();
  while (f && count < 64) {
    if (!f.isDirectory()) {
      strlcpy(files[count].name, f.name(), sizeof(files[count].name));
      files[count].mtime = f.getLastWrite();
      count++;
    }
    f.close();
    f = dir.openNextFile();
  }
  dir.close();

  if (count > 30) {
    for (int i = 0; i < count - 1; i++) {
      for (int j = i + 1; j < count; j++) {
        if (files[j].mtime < files[i].mtime) {
          auto tmp = files[i];
          files[i] = files[j];
          files[j] = tmp;
        }
      }
    }
    for (int i = 0; i < count - 30; i++) {
      char path[80] = "/rec/";
      strlcat(path, files[i].name, sizeof(path));
      SD_MMC.remove(path);
      Serial.println("[SD] Deleted: " + String(files[i].name));
    }
  }
}

// ═══════════════════════════════════════════════
// WEBSERVER handlers
// ═══════════════════════════════════════════════
void handleRoot() {
  String html = CONFIG_HTML;
  html.replace("%SSID%",  cfg.wifi_ssid);
  html.replace("%PASS%",  cfg.wifi_pass);
  html.replace("%CAM_ID%", cfg.camera_id);
  html.replace("%SERVER%", cfg.server_url);
  html.replace("%R0%", cfg.rotation == 0 ? "selected" : "");
  html.replace("%R1%", cfg.rotation == 1 ? "selected" : "");
  html.replace("%R2%", cfg.rotation == 2 ? "selected" : "");
  html.replace("%R3%", cfg.rotation == 3 ? "selected" : "");
  html.replace("%Q6%",  cfg.quality == 6  ? "selected" : "");
  html.replace("%Q8%",  cfg.quality == 8  ? "selected" : "");
  html.replace("%Q12%", cfg.quality == 12 ? "selected" : "");
  html.replace("%Q18%", cfg.quality == 18 ? "selected" : "");
  html.replace("%Q20%", cfg.quality == 20 ? "selected" : "");
  html.replace("%Q25%", cfg.quality == 25 ? "selected" : "");
  html.replace("%F1%",  cfg.fps_limit == 1  ? "selected" : "");
  html.replace("%F2%",  cfg.fps_limit == 2  ? "selected" : "");
  html.replace("%F5%",  cfg.fps_limit == 5  ? "selected" : "");
  html.replace("%F10%", cfg.fps_limit == 10 ? "selected" : "");
  html.replace("%IP%",   WiFi.localIP().toString());
  html.replace("%MEM%",  String(ESP.getFreeHeap() / 1024));
  html.replace("%PSRAM%", String(ESP.getPsramSize() / 1024));
  webServer.send(200, "text/html; charset=utf-8", html);
}

void handleSave() {
  if (webServer.method() != HTTP_POST) {
    webServer.send(405, "text/plain", "Method Not Allowed");
    return;
  }
  strlcpy(cfg.wifi_ssid,  webServer.arg("ssid").c_str(),   sizeof(cfg.wifi_ssid));
  strlcpy(cfg.wifi_pass,  webServer.arg("pass").c_str(),   sizeof(cfg.wifi_pass));
  strlcpy(cfg.camera_id,  webServer.arg("cam_id").c_str(),  sizeof(cfg.camera_id));
  strlcpy(cfg.server_url, webServer.arg("server").c_str(), sizeof(cfg.server_url));
  cfg.rotation  = webServer.arg("rotation").toInt();
  cfg.quality   = webServer.arg("quality").toInt();
  cfg.fps_limit = webServer.arg("fps").toInt();
  saveConfig();
  webServer.send(200, "text/html", "<html><body><h2>Config saved — rebooting...</h2>"
    "<meta http-equiv='refresh' content='2;url=/'></body></html>");
  delay(500);
  ESP.restart();
}

void handleRecordings() {
  File dir = SD_MMC.open("/rec");
  String html = REC_HTML_HEAD;

  if (!dir || !dir.isDirectory()) {
    html += "<div class='empty'>Aucun enregistrement</div>";
  } else {
    struct Entry { char name[64]; time_t mtime; };
    Entry files[64];
    int count = 0;
    File f = dir.openNextFile();
    while (f && count < 64) {
      if (!f.isDirectory()) {
        strlcpy(files[count].name, f.name(), sizeof(files[count].name));
        files[count].mtime = f.getLastWrite();
        count++;
      }
      f.close();
      f = dir.openNextFile();
    }
    dir.close();

    for (int i = 0; i < count - 1; i++) {
      for (int j = i + 1; j < count; j++) {
        if (files[j].mtime > files[i].mtime) {
          auto tmp = files[i];
          files[i] = files[j];
          files[j] = tmp;
        }
      }
    }

    if (count == 0) {
      html += "<div class='empty'>Aucun enregistrement</div>";
    } else {
      for (int i = 0; i < count && i < 24; i++) {
        html += "<div class='card'>";
        html += "<div class='hour'>" + String(files[i].name + 4) + "</div>";
        html += "<div class='btns'>";
        html += "<a class='btn play' href='/play/" + String(files[i].name) + "'>Play</a>";
        html += "<a class='btn dl' href='/dl/" + String(files[i].name) + "'>Download</a>";
        html += "</div></div>";
      }
    }
  }

  html += REC_HTML_FOOT;
  webServer.send(200, "text/html; charset=utf-8", html);
}

void handlePlay() {
  String path = "/" + webServer.uri().substring(6);
  if (!path.endsWith(".pcs")) { webServer.send(400, "text/plain", "Bad request"); return; }
  File f = SD_MMC.open(path.c_str(), FILE_READ);
  if (!f) { webServer.send(404, "text/plain", "Not found"); return; }

  String html = "<!DOCTYPE html><html><head>";
  html += "<meta charset=\"utf-8\"><title>Playback</title>";
  html += "<style>body{background:#000;margin:0;display:flex;flex-direction:column;align-items:center}";
  html += "img{max-width:95vw;max-height:90vh}";
  html += "#info{color:#888;font-family:monospace;position:fixed;bottom:10px}";
  html += "button{position:fixed;top:10px;right:10px;padding:8px 16px;background:#333;color:#fff;border:none;border-radius:6px;cursor:pointer}</style>";
  html += "</head><body>";
  html += "<img id='frame' src=''>";
  html += "<div id='info'></div>";
  html += "<button onclick='window.close()'>Fermer</button>";
  html += "<script>";
  html += "const f=document.getElementById('frame'),i=document.getElementById('info');";
  html += "let buf=null,pos=0,playing=true;";
  html += "function readU32(b,p){return(b[p]|b[p+1]<<8|b[p+2]<<16|b[p+3]<<24)>>>0;}";
  html += "function showFrame(){if(!buf||!playing)return;";
  html += "if(pos+4>buf.length){buf=null;fetch('/raw'+location.pathname).then(r=>r.arrayBuffer()).then(d=>{buf=new Uint8Array(d);pos=0;showFrame();}).catch(()=>{playing=false;});return;}";
  html += "const len=readU32(buf,pos);if(!len||pos+4+len>buf.length){pos+=4;if(pos+4<buf.length)showFrame();else{playing=false;}return;}const jpg=buf.subarray(pos+4,pos+4+len);";
  html += "f.src='data:image/jpeg;base64,'+btoa(String.fromCharCode.apply(null,jpg));";
  html += "i.textContent='Frame byte '+pos+' ('+len+'B)';pos+=4+len;setTimeout(showFrame,250);}";
  html += "showFrame();";
  html += "</script></body></html>";
  f.close();
  webServer.send(200, "text/html; charset=utf-8", html);
}

void handleDownload() {
  String path = "/" + webServer.uri().substring(4);
  if (!SD_MMC.exists(path.c_str())) { webServer.send(404, "text/plain", "Not found"); return; }
  File f = SD_MMC.open(path.c_str(), FILE_READ);
  size_t fileSize = f.size();

  webServer.client().println("HTTP/1.1 200 OK");
  webServer.client().println("Content-Type: application/octet-stream");
  webServer.client().println("Content-Disposition: attachment; filename=" + path.substring(5));
  webServer.client().println("Content-Length: " + String(fileSize));
  webServer.client().println();

  uint8_t buf[2048];
  size_t sent = 0;
  while (sent < fileSize) {
    size_t chunk = f.read(buf, sizeof(buf));
    if (chunk == 0) break;
    webServer.client().write(buf, chunk);
    sent += chunk;
  }
  f.close();
}

void handleRawRange() {
  String path = "/" + webServer.uri().substring(5);
  if (!SD_MMC.exists(path.c_str())) { webServer.send(404, "text/plain", "Not found"); return; }
  File f = SD_MMC.open(path.c_str(), FILE_READ);
  size_t fileSize = f.size();
  size_t start = 0, end = fileSize - 1;

  WiFiClient &client = webServer.client();
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: application/octet-stream");
  client.println("Accept-Ranges: bytes");
  client.println("Content-Length: " + String(fileSize));
  client.println();

  uint8_t buf[2048];
  size_t sent = 0;
  while (sent < fileSize) {
    size_t chunk = f.read(buf, sizeof(buf));
    if (chunk == 0) break;
    client.write(buf, chunk);
    sent += chunk;
  }
  f.close();
}

// ═══════════════════════════════════════════════
// TASK 1 — Capture caméra (Core 1, prio 3)
//
// RÈGLE ABSOLUE : esp_camera_fb_return() doit être appelé
// dans les 2 × période capteur après fb_get().
// → Pas de vTaskDelay. Boucle serrée. Zéro malloc.
// ═══════════════════════════════════════════════
void camTask(void *param) {
  Serial.println("[CAM] started");

  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) continue;   // ← pas de delay : drain le plus vite possible

    if (fb->len < 1000 || fb->len > FRAME_MAX) {
      esp_camera_fb_return(fb);
      continue;
    }

    int w = sCapIdx;
    size_t len = fb->len;

    // ── Copie vers slot stream ────────────────────────────────────
    memcpy(sBuf[w], fb->buf, len);
    sLen[w] = len;

    // ── Copie vers slot SD (non-bloquant, drop si pool vide) ─────
    uint8_t *slot = nullptr;
    if (xQueueReceive(qSdFree, &slot, 0) == pdTRUE && slot) {
      memcpy(slot, fb->buf, len);
      FrameItem fi = { slot, len };
      if (xQueueSend(qSdReady, &fi, 0) != pdTRUE) {
        xQueueSend(qSdFree, &slot, 0);   // rendre le slot si queue pleine
      }
    }

    esp_camera_fb_return(fb);   // ← IMMÉDIATEMENT après les copies, avant tout le reste

    // ── Publier le nouveau slot comme "latest" (mutex 1 ms max) ──
    if (xSemaphoreTake(sMux, pdMS_TO_TICKS(1)) == pdTRUE) {
      sLatestIdx = w;
      xSemaphoreGive(sMux);
    }
    // Avancer toujours, même si mutex timeout (fb déjà rendu → pas d'OVF)
    sCapIdx = (w + 1) % N_SBUF;
  }
}

// ═══════════════════════════════════════════════
// TASK 2 — Streaming HTTP POST (Core 0, prio 2)
//
// Copie la dernière frame SOUS mutex (~1 ms),
// puis fait le POST hors mutex (1-4 s).
// camTask n'est jamais bloqué par le POST.
// ═══════════════════════════════════════════════
void streamTask(void *param) {
  Serial.println("[STREAM] started");

  // Allouer le buffer HTTP une seule fois — jamais free/malloc pendant le stream
  httpBuf = (uint8_t*)ps_malloc(FRAME_MAX);
  if (!httpBuf) httpBuf = (uint8_t*)malloc(FRAME_MAX);
  if (!httpBuf) {
    Serial.println("[STREAM] FATAL: cannot alloc http buffer");
    vTaskDelete(NULL);
    return;
  }

  HTTPClient http;
  http.setReuse(true);
  String url = String(cfg.server_url) + "/stream_upload";

  uint32_t lastSent  = 0;
  uint32_t sentCount = 0;

  while (true) {
    if (!WiFi.isConnected()) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }

    uint32_t interval = (cfg.fps_limit > 0) ? 1000u / cfg.fps_limit : 200u;
    if (millis() - lastSent < interval) {
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }

    // ── Copier la dernière frame disponible SOUS mutex (~1 ms) ───
    size_t httpLen = 0;
    if (xSemaphoreTake(sMux, pdMS_TO_TICKS(20)) == pdTRUE) {
      int r = sLatestIdx;
      if (r >= 0 && sBuf[r] && sLen[r] > 0) {
        memcpy(httpBuf, sBuf[r], sLen[r]);
        httpLen = sLen[r];
      }
      xSemaphoreGive(sMux);
    }

    if (httpLen == 0) { vTaskDelay(pdMS_TO_TICKS(10)); continue; }

    // ── POST hors mutex — peut durer 1-4 s, camTask libre ────────
    http.begin(url);
    http.addHeader("X-API-Key",    cfg.camera_id);
    http.addHeader("Content-Type", "image/jpeg");
    http.setTimeout(4000);
    int code = http.POST(httpBuf, httpLen);

    lastSent = millis();
    sentCount++;

    if (code == 200 || code == 204) {
      if (sentCount % 30 == 0)
        Serial.printf("[STREAM] %u frames sent, heap=%uKB\n",
          sentCount, ESP.getFreeHeap() / 1024);
    } else {
      Serial.printf("[STREAM] HTTP %d (%d B)\n", code, httpLen);
    }
  }
}

// ═══════════════════════════════════════════════
// TASK 3 — SD Recording (Core 0, prio 1)
// Défile les slots pré-alloués, écrit sur SD, rend le slot.
// ═══════════════════════════════════════════════
void sdTask(void *param) {
  Serial.println("[SD] started");

  FrameItem item;
  while (true) {
    if (xQueueReceive(qSdReady, &item, pdMS_TO_TICKS(5000)) != pdTRUE) {
      // timeout — flush périodique
      if (sdReady && recFile && recFrameCount > 0 && millis() - lastSdFlush > 60000) {
        recFile.flush();
        lastSdFlush = millis();
      }
      continue;
    }

    if (sdReady) {
      String hourKey = getHourKey();
      if (hourKey != currentHourKey) {
        if (recFile) { recFile.flush(); recFile.close(); }
        recFile = SD_MMC.open(getFilePath(hourKey).c_str(), FILE_APPEND);
        currentHourKey = hourKey;
        recFrameCount  = 0;
        lastSdFlush    = millis();
        Serial.println("[SD] New file: " + hourKey);
        purgeOldRecordings();
      }
      if (recFile) {
        uint32_t flen = (uint32_t)item.len;
        recFile.write((uint8_t*)&flen, 4);
        recFile.write(item.data, item.len);
        recFrameCount++;
        if (millis() - lastSdFlush > 60000 || recFrameCount % 100 == 0) {
          recFile.flush();
          lastSdFlush = millis();
        }
      }
    }

    // Rendre le slot au pool (jamais free())
    xQueueSend(qSdFree, &item.data, portMAX_DELAY);
  }
}

// ═══════════════════════════════════════════════
// AP + Config WebServer
// ═══════════════════════════════════════════════
void startConfigMode() {
  Serial.println("[WIFI] Starting AP...");
  WiFi.mode(WIFI_AP);
  WiFi.softAP("PCS-Setup", "12345678");
  IPAddress IP = WiFi.softAPIP();
  Serial.print("[WIFI] AP IP: ");
  Serial.println(IP);

  webServer.on("/", handleRoot);
  webServer.on("/save", handleSave);
  webServer.on("/recordings", handleRecordings);
  webServer.on("/play/", handlePlay);
  webServer.on("/dl/", handleDownload);
  webServer.on("/raw", handleRawRange);
  webServer.begin();
  Serial.println("[WEB] Config server started");

  configMode = true;
  while (true) {
    webServer.handleClient();
    delay(10);
  }
}

// ═══════════════════════════════════════════════
// SETUP
// ═══════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("╔══════════════════════════════════════╗");
  Serial.println("║   ParkingCamSecurity — ESP32-CAM    ║");
  Serial.println("╚══════════════════════════════════════╝");

  // Bouton config enfoncé → mode AP
  pinMode(CONFIG_BTN_PIN, INPUT_PULLUP);
  if (digitalRead(CONFIG_BTN_PIN) == LOW) {
    Serial.println("[BOOT] Config button pressed — AP mode");
    startConfigMode();
  }

  loadConfig();

  if (strlen(cfg.wifi_ssid) == 0) {
    Serial.println("[BOOT] No WiFi config — AP mode");
    startConfigMode();
  }

  // WiFi
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true); // Reset WiFi
  delay(100);
  WiFi.setAutoReconnect(true);
  
  Serial.print("[WIFI] Tentative de connexion au SSID: '");
  Serial.print(cfg.wifi_ssid);
  Serial.print("' avec pass: '");
  Serial.print(cfg.wifi_pass);
  Serial.println("'");

  WiFi.begin(cfg.wifi_ssid, cfg.wifi_pass);

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 60000) {
    delay(500);
    Serial.print(".");
    // Affiche le code d'état : 3=connecté, 6=déconnecté, 1=SSID non trouvé, 4=échec connexion
    Serial.print(WiFi.status()); 
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[WIFI] Failed — starting AP");
    startConfigMode();
  }

  Serial.println("\n[WIFI] Connected: " + WiFi.localIP().toString());

  delay(500);

  // ── Pré-allocation des buffers (UNE SEULE FOIS, zéro fragmentation) ──
  sMux = xSemaphoreCreateMutex();
  for (int i = 0; i < N_SBUF; i++) {
    sBuf[i] = (uint8_t*)ps_malloc(FRAME_MAX);
    if (!sBuf[i]) sBuf[i] = (uint8_t*)malloc(FRAME_MAX);
    if (!sBuf[i]) { Serial.printf("[FATAL] sBuf[%d] alloc fail\n", i); while(true) delay(1000); }
  }
  qSdFree  = xQueueCreate(N_SDBUF, sizeof(uint8_t*));
  qSdReady = xQueueCreate(N_SDBUF, sizeof(FrameItem));
  for (int i = 0; i < N_SDBUF; i++) {
    sdPool[i] = (uint8_t*)ps_malloc(FRAME_MAX);
    if (!sdPool[i]) sdPool[i] = (uint8_t*)malloc(FRAME_MAX);
    if (sdPool[i]) xQueueSend(qSdFree, &sdPool[i], 0);
  }
  Serial.printf("[INIT] Buffers OK — heap=%uKB PSRAM=%uKB\n",
    ESP.getFreeHeap()/1024, ESP.getPsramSize()/1024);

  // ── Caméra ──────────────────────────────────────────────────────
  if (!initCamera()) {
    Serial.println("[FATAL] Camera init failed");
    while (true) delay(1000);
  }

  // ── SD ───────────────────────────────────────────────────────────
  sdReady = initSD();

  // ── NTP ──────────────────────────────────────────────────────────
  syncNTP();

  // ── 3 tâches FreeRTOS ────────────────────────────────────────────
  // camTask    Core 1 prio 3 : capture serrée, jamais bloquée
  // streamTask Core 0 prio 2 : HTTP POST
  // sdTask     Core 0 prio 1 : écriture SD
  xTaskCreatePinnedToCore(camTask,    "cam",    4096, NULL, 3, NULL, 1);
  xTaskCreatePinnedToCore(streamTask, "stream", 8192, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(sdTask,     "sd",     8192, NULL, 1, NULL, 0);

  Serial.println("[BOOT] All tasks started");
  Serial.printf("[BOOT] heap=%uKB PSRAM=%uKB\n",
    ESP.getFreeHeap()/1024, ESP.getPsramSize()/1024);
}

// ═══════════════════════════════════════════════
// LOOP — webserver + monitoring
// ═══════════════════════════════════════════════
void loop() {
  // Si en mode config, le webserver tourne déjà dans startConfigMode()
  if (!configMode) {
    delay(100);

    // Monitoring mémoire toutes les 30s
    static unsigned long lastMemReport = 0;
    if (millis() - lastMemReport > 30000) {
      lastMemReport = millis();
      UBaseType_t sdFree  = uxQueueMessagesWaiting(qSdFree);
      UBaseType_t sdReady = uxQueueMessagesWaiting(qSdReady);
      Serial.printf("[MON] heap=%uKB sd_pool=%d free %d ready latest=%d\n",
        ESP.getFreeHeap()/1024, (int)sdFree, (int)sdReady, (int)sLatestIdx);
    }
  }
}
