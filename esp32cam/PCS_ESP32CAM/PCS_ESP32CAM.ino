/*
 * PCS_ESP32CAM.ino
 * ParkingCamSecurity — Firmware ESP32-CAM (AI Thinker OV2640)
 *
 * Fonctionnalités :
 *  - Page de configuration web (WiFi, Camera ID, rotation, qualité, FPS)
 *  - Stream MJPEG vers le serveur PCS via HTTP POST
 *  - Enregistrement SD carte 24h (fichiers horaires, suppression auto)
 *  - Interface web pour visualiser et télécharger les fragments par heure
 *
 * Librairies requises (Arduino IDE) :
 *  - ESP32 Arduino Core (Espressif)  — inclus : WiFi, WebServer, Preferences, SD_MMC, esp_camera
 *
 * SD recommandée : 16 Go (≈8.6 Go / 24h à QVGA quality=12 10fps)
 *
 * MODE CONFIG : maintenir GPIO 13 appuyé au démarrage
 *   → AP "PCS-Config" sans mot de passe → ouvrir http://192.168.4.1
 *
 * MODE NORMAL : se connecte au WiFi configuré, stream + enregistre
 *   → Config accessible sur http://<IP_ESP>/
 *   → Enregistrements sur http://<IP_ESP>/recordings
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
#include "freertos/semphr.h"

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

// Bouton config : GPIO 13 appuyé au boot → mode AP
#define CONFIG_BTN_PIN  13

// ═══════════════════════════════════════════════
// CONFIGURATION
// ═══════════════════════════════════════════════
struct Config {
  char wifi_ssid[64]   = "";
  char wifi_pass[64]   = "";
  char camera_id[33]   = "esp32cam-001";
  char server_url[128] = "https://web-production-10852.up.railway.app";
  int  rotation        = 0;   // 0=normal 1=180° 2=miroir-H 3=miroir-V
  int  quality         = 12;  // JPEG quality (0-63, bas = meilleure qualité)
  int  fps_limit       = 10;  // FPS max envoyés au serveur
};

Config cfg;
Preferences prefs;
WebServer webServer(80);

bool configMode = false;
bool sdReady    = false;
bool ntpReady   = false;

SemaphoreHandle_t camMutex;

// Fichier d'enregistrement en cours
File    recFile;
String  currentHourKey = "";   // "YYYY-MM-DD_HH"
uint32_t recFrameCount = 0;

// ═══════════════════════════════════════════════
// HTML — Page de configuration
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
.row{display:flex;gap:10px}
.row>div{flex:1}
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
    <label>SSID (nom du réseau)</label>
    <input name="ssid" value="%SSID%" placeholder="MonReseau" required autocomplete="off" autocapitalize="none">
    <div class="hint">Fonctionne avec les réseaux cachés (hidden SSID) — entrez le nom exact</div>
    <label>Mot de passe</label>
    <input name="pass" type="password" value="%PASS%" placeholder="••••••••" autocomplete="new-password">
    <div class="hint">Laisser vide si réseau ouvert</div>

    <h2>Identité caméra</h2>
    <label>Camera ID (= API Key)</label>
    <input name="cam_id" value="%CAM_ID%" placeholder="parking-entree-01" maxlength="32" required pattern="[a-zA-Z0-9_-]+" autocapitalize="none">
    <div class="hint">Doit correspondre à la clé API saisie dans le dashboard PCS</div>
    <label>URL du serveur PCS</label>
    <input name="server" value="%SERVER%" placeholder="https://..." required>

    <h2>Image</h2>
    <label>Rotation / Orientation</label>
    <select name="rotation">
      <option value="0" %R0%>Normal (0°)</option>
      <option value="1" %R1%>180° retourné (caméra à l'envers)</option>
      <option value="2" %R2%>Miroir horizontal</option>
      <option value="3" %R3%>Miroir vertical</option>
    </select>
    <div class="row">
      <div>
        <label>Qualité JPEG</label>
        <select name="quality">
          <option value="8"  %Q8% >Haute  (8) — ~14 Go/24h</option>
          <option value="12" %Q12%>Normale (12) — ~9 Go/24h</option>
          <option value="20" %Q20%>Économe (20) — ~4 Go/24h</option>
        </select>
      </div>
      <div>
        <label>FPS serveur</label>
        <select name="fps">
          <option value="5"  %F5% >5 fps</option>
          <option value="10" %F10%>10 fps</option>
          <option value="15" %F15%>15 fps</option>
        </select>
      </div>
    </div>
    <button type="submit">Sauvegarder et redémarrer</button>
  </form>
  <a class="link" href="/recordings">📼 Enregistrements SD</a>
  <div class="ip">IP : %IP%</div>
</div>
</body></html>
)HTML";

// ═══════════════════════════════════════════════
// HTML — Page enregistrements (header + footer)
// ═══════════════════════════════════════════════
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
<div class="sub">Dernières 24h — cliquez Play pour visionner dans le navigateur ou Télécharger</div>
<div class="grid">
)HTML";

const char REC_HTML_FOOT[] PROGMEM = R"HTML(
</div></body></html>
)HTML";

// ═══════════════════════════════════════════════
// GESTION CONFIGURATION
// ═══════════════════════════════════════════════
void loadConfig() {
  prefs.begin("pcs", true);
  strlcpy(cfg.wifi_ssid,  prefs.getString("ssid",    "").c_str(),       sizeof(cfg.wifi_ssid));
  strlcpy(cfg.wifi_pass,  prefs.getString("pass",    "").c_str(),       sizeof(cfg.wifi_pass));
  strlcpy(cfg.camera_id,  prefs.getString("cam_id",  "esp32cam-001").c_str(), sizeof(cfg.camera_id));
  strlcpy(cfg.server_url, prefs.getString("server",  "https://web-production-10852.up.railway.app").c_str(), sizeof(cfg.server_url));
  cfg.rotation  = prefs.getInt("rotation", 0);
  cfg.quality   = prefs.getInt("quality",  12);
  cfg.fps_limit = prefs.getInt("fps",      10);
  prefs.end();
}

void saveConfig() {
  prefs.begin("pcs", false);
  prefs.putString("ssid",    cfg.wifi_ssid);
  prefs.putString("pass",    cfg.wifi_pass);
  prefs.putString("cam_id",  cfg.camera_id);
  prefs.putString("server",  cfg.server_url);
  prefs.putInt("rotation",   cfg.rotation);
  prefs.putInt("quality",    cfg.quality);
  prefs.putInt("fps",        cfg.fps_limit);
  prefs.end();
}

// ═══════════════════════════════════════════════
// INIT CAMÉRA
// ═══════════════════════════════════════════════
bool initCamera() {
  camera_config_t config;
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
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_QVGA;  // 320x240 — bon équilibre qualité/débit
  config.jpeg_quality = cfg.quality;
  config.fb_count     = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  // Appliquer la rotation
  sensor_t *s = esp_camera_sensor_get();
  switch (cfg.rotation) {
    case 0: s->set_vflip(s, 0); s->set_hmirror(s, 0); break;
    case 1: s->set_vflip(s, 1); s->set_hmirror(s, 1); break;  // 180°
    case 2: s->set_vflip(s, 0); s->set_hmirror(s, 1); break;  // miroir H
    case 3: s->set_vflip(s, 1); s->set_hmirror(s, 0); break;  // miroir V
  }
  return true;
}

// ═══════════════════════════════════════════════
// INIT SD (mode 1-bit pour compatibilité caméra)
// ═══════════════════════════════════════════════
bool initSD() {
  if (!SD_MMC.begin("/sdcard", true)) {  // true = 1-bit mode
    Serial.println("SD mount failed");
    return false;
  }
  if (SD_MMC.cardType() == CARD_NONE) {
    Serial.println("No SD card");
    return false;
  }
  // Créer le dossier d'enregistrements
  if (!SD_MMC.exists("/rec")) {
    SD_MMC.mkdir("/rec");
  }
  Serial.printf("SD OK — %llu MB\n", SD_MMC.cardSize() / (1024 * 1024));
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
  Serial.printf("NTP: %s\n", ntpReady ? "OK" : "FAIL (heure non sync)");
}

// ═══════════════════════════════════════════════
// HELPERS — Heure courante
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

// Format fichier .pcs : suite de [uint32_t taille][données JPEG]
void writeFrameToSD(const uint8_t *data, size_t len) {
  String hourKey = getHourKey();

  // Nouveau fichier si on change d'heure
  if (hourKey != currentHourKey) {
    if (recFile) recFile.close();
    String path = getFilePath(hourKey);
    recFile = SD_MMC.open(path, FILE_WRITE);
    currentHourKey = hourKey;
    recFrameCount  = 0;
    Serial.println("New recording: " + path);

    // Supprimer les fichiers de plus de 24h
    
  }

  if (!recFile) return;

  uint32_t flen = (uint32_t)len;
  recFile.write((uint8_t*)&flen, 4);
  recFile.write(data, len);
  recFile.flush();
  recFrameCount++;
}

void purgeOldRecordings() {
  File dir = SD_MMC.open("/rec");
  if (!dir || !dir.isDirectory()) return;

  // On garde max 25 fichiers (1 par heure + 1 en cours)
  // Chaque fichier = 1h, donc 24 fichiers = 24h
  struct FileEntry { String name; };
  FileEntry files[64];
  int count = 0;

  File f = dir.openNextFile();
  while (f && count < 64) {
    if (!f.isDirectory()) {
      files[count++].name = String(f.name());
    }
    f = dir.openNextFile();
  }
  dir.close();

  // Tri simple (noms = dates, tri alphabétique = tri chronologique)
  for (int i = 0; i < count - 1; i++)
    for (int j = i + 1; j < count; j++)
      if (files[i].name > files[j].name)
        swap(files[i].name, files[j].name);

  // Supprimer les plus anciens si on dépasse 25 fichiers
  while (count > 25) {
    String path = "/rec/" + files[0].name;
    SD_MMC.remove(path);
    Serial.println("Purged: " + path);
    for (int i = 0; i < count - 1; i++) files[i] = files[i + 1];
    count--;
  }
}

// ═══════════════════════════════════════════════
// SERVEUR WEB — Handlers
// ═══════════════════════════════════════════════

// Remplace les placeholders dans le HTML de config
String buildConfigPage() {
  String html = FPSTR(CONFIG_HTML);
  html.replace("%SSID%",   String(cfg.wifi_ssid));
  html.replace("%PASS%",   String(cfg.wifi_pass));
  html.replace("%CAM_ID%", String(cfg.camera_id));
  html.replace("%SERVER%", String(cfg.server_url));
  html.replace("%R0%", cfg.rotation == 0 ? "selected" : "");
  html.replace("%R1%", cfg.rotation == 1 ? "selected" : "");
  html.replace("%R2%", cfg.rotation == 2 ? "selected" : "");
  html.replace("%R3%", cfg.rotation == 3 ? "selected" : "");
  html.replace("%Q8%",  cfg.quality == 8  ? "selected" : "");
  html.replace("%Q12%", cfg.quality == 12 ? "selected" : "");
  html.replace("%Q20%", cfg.quality == 20 ? "selected" : "");
  html.replace("%F5%",  cfg.fps_limit == 5  ? "selected" : "");
  html.replace("%F10%", cfg.fps_limit == 10 ? "selected" : "");
  html.replace("%F15%", cfg.fps_limit == 15 ? "selected" : "");
  html.replace("%IP%",  WiFi.localIP().toString());
  return html;
}

void handleRoot() {
  webServer.send(200, "text/html", buildConfigPage());
}

void handleSave() {
  if (webServer.method() != HTTP_POST) {
    webServer.send(405, "text/plain", "Method Not Allowed");
    return;
  }
  strlcpy(cfg.wifi_ssid,  webServer.arg("ssid").c_str(),    sizeof(cfg.wifi_ssid));
  strlcpy(cfg.wifi_pass,  webServer.arg("pass").c_str(),    sizeof(cfg.wifi_pass));
  strlcpy(cfg.camera_id,  webServer.arg("cam_id").c_str(),  sizeof(cfg.camera_id));
  strlcpy(cfg.server_url, webServer.arg("server").c_str(),  sizeof(cfg.server_url));
  cfg.rotation  = webServer.arg("rotation").toInt();
  cfg.quality   = webServer.arg("quality").toInt();
  cfg.fps_limit = webServer.arg("fps").toInt();
  saveConfig();

  webServer.send(200, "text/html",
    "<html><body style='font-family:sans-serif;padding:30px'>"
    "<h2 style='color:#16a34a'>✓ Configuration sauvegardée</h2>"
    "<p>Redémarrage dans 2 secondes...</p>"
    "</body></html>");
  delay(2000);
  ESP.restart();
}

void handleRecordings() {
  String page = FPSTR(REC_HTML_HEAD);

  File dir = SD_MMC.open("/rec");
  bool any = false;

  if (dir && dir.isDirectory()) {
    // Collecter et trier
    String names[64];
    int count = 0;
    File f = dir.openNextFile();
    while (f && count < 64) {
      if (!f.isDirectory()) names[count++] = String(f.name());
      f = dir.openNextFile();
    }
    dir.close();
    // Tri décroissant (plus récent en premier)
    for (int i = 0; i < count - 1; i++)
      for (int j = i + 1; j < count; j++)
        if (names[i] < names[j]) swap(names[i], names[j]);

    for (int i = 0; i < count; i++) {
      String fname = names[i];
      // Nom affiché : "2024-01-15 14h00 – 14h59"
      String display = fname;
      display.replace("_", " ");
      display.replace(".pcs", "h00 – ");
      // Taille
      String path = "/rec/" + fname;
      File fi = SD_MMC.open(path);
      long sz = fi ? fi.size() : 0;
      if (fi) fi.close();
      String szStr = sz > 1048576 ? String(sz / 1048576) + " Mo" : String(sz / 1024) + " Ko";

      page += "<div class='card'>";
      page += "<div class='hour'>📼 " + display + "</div>";
      page += "<div class='meta'>" + szStr + " · " + String(fname) + "</div>";
      page += "<div class='btns'>";
      page += "<a class='btn play' href='/stream?f=" + fname + "'>▶ Play</a>";
      page += "<a class='btn dl'   href='/dl?f="     + fname + "'>⬇ Télécharger</a>";
      page += "</div></div>";
      any = true;
    }
  }

  if (!any) {
    page += "<div class='empty'><p>Aucun enregistrement sur la carte SD.</p>"
            "<p style='margin-top:8px;font-size:12px'>Les fichiers apparaissent heure par heure.</p></div>";
  }

  page += FPSTR(REC_HTML_FOOT);
  webServer.send(200, "text/html", page);
}

// Lecture d'un fichier .pcs et envoi en MJPEG (lecture directe dans le navigateur)
void handleStream() {
  if (!webServer.hasArg("f")) { webServer.send(400, "text/plain", "Missing ?f="); return; }
  String fname = webServer.arg("f");
  // Sécurité : pas de path traversal
  if (fname.indexOf('/') >= 0 || fname.indexOf("..") >= 0) {
    webServer.send(403, "text/plain", "Forbidden"); return;
  }
  String path = "/rec/" + fname;
  File f = SD_MMC.open(path);
  if (!f) { webServer.send(404, "text/plain", "Not found"); return; }

  WiFiClient client = webServer.client();
  client.print("HTTP/1.1 200 OK\r\n");
  client.print("Content-Type: multipart/x-mixed-replace; boundary=frame\r\n");
  client.print("Cache-Control: no-cache\r\n\r\n");

  while (f.available() >= 4 && client.connected()) {
    uint32_t flen;
    f.read((uint8_t*)&flen, 4);
    if (flen == 0 || flen > 100000) break;

    uint8_t *buf = (uint8_t*)malloc(flen);
    if (!buf) break;
    size_t read = f.read(buf, flen);

    if (read == flen) {
      client.print("--frame\r\n");
      client.print("Content-Type: image/jpeg\r\n");
      client.printf("Content-Length: %u\r\n\r\n", flen);
      client.write(buf, flen);
      client.print("\r\n");
      delay(33);  // ~30fps playback
    }
    free(buf);
  }
  f.close();
}

// Téléchargement brut du fichier .pcs
void handleDownload() {
  if (!webServer.hasArg("f")) { webServer.send(400, "text/plain", "Missing ?f="); return; }
  String fname = webServer.arg("f");
  if (fname.indexOf('/') >= 0 || fname.indexOf("..") >= 0) {
    webServer.send(403, "text/plain", "Forbidden"); return;
  }
  String path = "/rec/" + fname;
  File f = SD_MMC.open(path);
  if (!f) { webServer.send(404, "text/plain", "Not found"); return; }

  webServer.sendHeader("Content-Disposition", "attachment; filename=\"" + fname + "\"");
  webServer.streamFile(f, "application/octet-stream");
  f.close();
}

void handleNotFound() {
  webServer.send(404, "text/plain", "Not found");
}

// ═══════════════════════════════════════════════
// STREAM VERS SERVEUR PCS (tâche FreeRTOS — Core 0)
// ═══════════════════════════════════════════════
void taskStream(void *param) {
  const uint32_t frameInterval = 1000 / cfg.fps_limit;
  uint32_t lastSent = 0;

  for (;;) {
    if (!WiFi.isConnected()) { vTaskDelay(1000 / portTICK_PERIOD_MS); continue; }

    uint32_t now = millis();
    if (now - lastSent < frameInterval) {
      vTaskDelay(5 / portTICK_PERIOD_MS);
      continue;
    }

    if (xSemaphoreTake(camMutex, 100 / portTICK_PERIOD_MS) == pdTRUE) {
      camera_fb_t *fb = esp_camera_fb_get();
      if (fb) {
        // Écriture SD
        if (sdReady) writeFrameToSD(fb->buf, fb->len);

        // Envoi HTTP vers PCS
        HTTPClient http;
        String url = String(cfg.server_url) + "/stream_upload";
        http.begin(url);
        http.addHeader("X-API-Key", cfg.camera_id);

        // Construction multipart manuellement (plus rapide que HTTPClient multipart)
        String boundary = "PCSboundary";
        String bodyHead = "--" + boundary + "\r\n"
                          "Content-Disposition: form-data; name=\"image\"; filename=\"f.jpg\"\r\n"
                          "Content-Type: image/jpeg\r\n\r\n";
        String bodyTail = "\r\n--" + boundary + "--\r\n";

        uint8_t *payload = (uint8_t*)malloc(bodyHead.length() + fb->len + bodyTail.length());
        if (payload) {
          memcpy(payload, bodyHead.c_str(), bodyHead.length());
          memcpy(payload + bodyHead.length(), fb->buf, fb->len);
          memcpy(payload + bodyHead.length() + fb->len, bodyTail.c_str(), bodyTail.length());

          http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
          http.POST(payload, bodyHead.length() + fb->len + bodyTail.length());
          free(payload);
        }
        http.end();
        esp_camera_fb_return(fb);
        lastSent = millis();
      }
      xSemaphoreGive(camMutex);
    }
    vTaskDelay(2 / portTICK_PERIOD_MS);
  }
}

// ═══════════════════════════════════════════════
// SETUP
// ═══════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== PCS ESP32-CAM Firmware ===");

  // Bouton config
  pinMode(CONFIG_BTN_PIN, INPUT_PULLUP);
  loadConfig();

  // Forcer mode config si bouton appuyé au boot OU si pas de SSID
  configMode = (digitalRead(CONFIG_BTN_PIN) == LOW) || (strlen(cfg.wifi_ssid) == 0);

  camMutex = xSemaphoreCreateMutex();

  // Init caméra
  if (!initCamera()) {
    Serial.println("FATAL: camera init failed");
    delay(3000);
    ESP.restart();
  }

  // Init SD
  sdReady = initSD();
  if (!sdReady) Serial.println("WARNING: SD not available, recording disabled");

  if (configMode) {
    // ── MODE AP CONFIGURATION ──
    Serial.println(">>> MODE CONFIG AP <<<");
    WiFi.softAP("PCS-Config");
    Serial.print("AP IP: "); Serial.println(WiFi.softAPIP());

    webServer.on("/",         HTTP_GET,  handleRoot);
    webServer.on("/save",     HTTP_POST, handleSave);
    webServer.on("/recordings", HTTP_GET, handleRecordings);
    webServer.onNotFound(handleNotFound);
    webServer.begin();
    Serial.println("Config server started — http://192.168.4.1");

  } else {
    // ── MODE NORMAL ──
    Serial.printf("Connecting to WiFi: %s\n", cfg.wifi_ssid);
    WiFi.mode(WIFI_STA);
    // Connexion même aux SSID cachés (hidden network)
    WiFi.begin(cfg.wifi_ssid, cfg.wifi_pass, 0, NULL, true);

    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries++ < 30) {
      delay(500); Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("\nWiFi OK — IP: %s\n", WiFi.localIP().toString().c_str());
      syncNTP();
    } else {
      Serial.println("\nWiFi FAIL — passage en mode config");
      delay(1000);
      configMode = true;
      WiFi.softAP("PCS-Config");
    }

    // Serveur web (config + enregistrements)
    webServer.on("/",           HTTP_GET,  handleRoot);
    webServer.on("/save",       HTTP_POST, handleSave);
    webServer.on("/recordings", HTTP_GET,  handleRecordings);
    webServer.on("/stream",     HTTP_GET,  handleStream);
    webServer.on("/dl",         HTTP_GET,  handleDownload);
    webServer.onNotFound(handleNotFound);
    webServer.begin();
    Serial.println("Web server started on port 80");

    // Tâche stream sur Core 0
    xTaskCreatePinnedToCore(taskStream, "StreamTask", 8192, NULL, 1, NULL, 0);
  }
}

// ═══════════════════════════════════════════════
// LOOP
// ═══════════════════════════════════════════════
void loop() {
  webServer.handleClient();

  // Reconnexion WiFi auto si déconnecté
  static uint32_t lastCheck = 0;
  if (!configMode && millis() - lastCheck > 10000) {
    lastCheck = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi lost — reconnecting...");
      WiFi.reconnect();
    }
  }

  delay(1);
}
