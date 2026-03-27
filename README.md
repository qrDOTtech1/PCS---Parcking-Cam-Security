# ParkingCamSecurity (PCS)

**Système de vidéosurveillance de parkings — ESP32-CAM, webcam, smartphone ou toute caméra IP.**

> Stream live · Reconnaissance de plaques (OCR) · Enregistrement SD 24h · Alertes blacklist · Déployable sur Railway en 1 clic

---

## Aperçu

PCS est une plateforme web Flask qui centralise plusieurs caméras de surveillance dans un seul dashboard.
Chaque caméra envoie ses frames en HTTP (multipart POST) — qu'il s'agisse d'un ESP32-CAM, d'un téléphone, d'une webcam ou de n'importe quel appareil supportant le firmware fourni.

```
[ESP32-CAM]  ──┐
[Téléphone]  ──┤──► /stream_upload ──► Dashboard live
[Webcam PC]  ──┘         │
                          └──► OCR Plate Recognizer ──► Alerte blacklist
```

---

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| **Dashboard live** | Vignettes toutes caméras, polling 500 ms |
| **Camera view** | Flux MJPEG continu (vrai stream, pas de polling) |
| **Camera setup** | Page `/setup` — utiliser téléphone/webcam sans connexion |
| **Reconnaissance plaques** | Intégration Plate Recognizer API (OCR) |
| **Blacklist** | Alertes temps réel via Socket.IO si plaque connue |
| **Carte GPS** | Positionnement caméras sur carte Leaflet |
| **Enregistrement SD** | Firmware ESP32 — 24h en fichiers horaires, lecture/DL depuis le navigateur |
| **Déploiement Railway** | Procfile + Dockerfile prêts, SQLite ou MySQL |

---

## Démarrage rapide

### 1. Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `FLASK_SECRET_KEY` | Clé secrète Flask | `parkingcam-secret-key-change-in-prod` |
| `MASTER_KEY` | Clé admin pour créer des comptes | `master_key_pcs_2024` |
| `DATABASE_URL` | URI SQLAlchemy | `sqlite:///parkingcam.db` |

> **Railway** : ajouter ces variables dans *Settings → Variables*.
> Pour SQLite persistant sur Railway, monter un volume sur `/app/data` et mettre `DATABASE_URL=sqlite:////app/data/parkingcam.db`.

### 2. Lancer en local

```bash
pip install -r requirements.txt
python app.py
```

### 3. Déployer sur Railway

1. Fork ou push ce repo sur GitHub
2. Nouveau projet Railway → *Deploy from GitHub repo*
3. Ajouter les variables d'environnement ci-dessus
4. *(Optionnel)* Ajouter un plugin **Volume** monté sur `/app/data` pour persister la DB

---

## Ajouter une caméra

### Option A — ESP32-CAM (matériel)

Flasher `esp32cam/PCS_ESP32CAM.ino` via Arduino IDE.
Au premier démarrage (ou GPIO 13 appuyé) → AP WiFi **PCS-Config** → ouvrir **http://192.168.4.1** → remplir SSID, mot de passe, Camera ID, URL serveur.

### Option B — Téléphone / Webcam (navigateur)

1. Ouvrir `https://<votre-serveur>/setup`
2. Choisir la caméra du device, définir un Camera ID
3. Cliquer **Start streaming** — le flux part immédiatement
4. Se connecter au dashboard → **Add Camera** → saisir le même ID comme API Key

### Option C — Script Python (test / CI)

```bash
python webcam_tester.py --url https://<serveur> --key mon-camera-id
```

---

## Structure du projet

```
ParkingCamSecurity/
│
├── app.py                  # Application Flask + Socket.IO + routes API
├── models.py               # Modèles SQLAlchemy (User, Camera, Blacklist…)
├── requirements.txt        # Dépendances Python
├── Procfile                # gunicorn eventlet (Railway / Heroku)
├── Dockerfile              # Image Docker
│
├── esp32cam/
│   └── PCS_ESP32CAM.ino   # Firmware ESP32-CAM complet
│       ├── Config web (WiFi, ID, rotation, qualité)
│       ├── Stream HTTP vers PCS
│       ├── Enregistrement SD horaire (24h)
│       └── Lecture / téléchargement fragments
│
├── templates/
│   ├── login.html          # Page de connexion + bouton "Create a Camera"
│   ├── dashboard.html      # Grille caméras live + blacklist + carte GPS
│   ├── camera_view.html    # Vue MJPEG plein écran + sidebar caméras
│   ├── camera_setup.html   # Setup caméra sans connexion (/setup)
│   └── terms.html          # CGU bilingues EN/FR
│
├── static/
│   └── img/
│       └── PCS_wordmark_logo.svg
│
├── webcam_tester.py        # Simulateur ESP32 (webcam PC → serveur)
├── stream_tester.py        # Test flux vidéo
└── admin_tools.py          # Outils admin
```

---

## API Endpoints

### Authentification & UI

| Endpoint | Méthode | Auth | Description |
|---|---|---|---|
| `/login` | GET/POST | — | Connexion |
| `/logout` | GET | session | Déconnexion |
| `/dashboard` | GET | session | Interface principale |
| `/camera/<id>` | GET | session | Vue MJPEG caméra |
| `/setup` | GET | — | Setup caméra (sans connexion) |
| `/terms` | GET | — | Conditions d'utilisation |

### API Caméra (firmware / scripts)

| Endpoint | Méthode | Auth | Description |
|---|---|---|---|
| `/stream_upload` | POST | `X-API-Key` header | Envoyer une frame JPEG |
| `/ping` | GET | `X-API-Key` header | Heartbeat + récupérer config |
| `/upload` | POST | `X-API-Key` header | Analyse OCR plaque |
| `/public_stream/<id>` | POST | — | Stream pré-enregistrement (navigateur) |

### Flux vidéo

| Endpoint | Auth | Description |
|---|---|---|
| `/video_feed/<id>` | session | Dernière frame JPEG |
| `/mjpeg_stream/<id>` | session | Flux MJPEG continu (multipart) |

### Dashboard (POST)

| Endpoint | Description |
|---|---|
| `/dashboard/add_camera` | Enregistrer une caméra (name + api_key) |
| `/dashboard/del_camera/<id>` | Supprimer une caméra |
| `/dashboard/add_blacklist` | Ajouter une plaque à la blacklist |
| `/dashboard/update_camera_gps/<id>` | Mettre à jour position GPS |
| `/dashboard/update_config` | Sauvegarder token Plate Recognizer |

---

## Firmware ESP32-CAM

**Fichier** : `esp32cam/PCS_ESP32CAM.ino`
**Matériel** : AI Thinker ESP32-CAM (OV2640) + carte SD 16 Go minimum

### Modes de démarrage

| Mode | Condition | Accès |
|---|---|---|
| **Config AP** | GPIO 13 appuyé au boot, ou aucun SSID configuré | WiFi `PCS-Config` → http://192.168.4.1 |
| **Normal** | SSID configuré | http://`<IP locale>`/ |

### Page de configuration (`/`)

- SSID + mot de passe WiFi *(réseaux cachés supportés — entrer le nom exact)*
- Camera ID = API Key PCS
- URL du serveur
- Rotation : Normal / 180° / Miroir H / Miroir V
- Qualité JPEG + FPS envoyés au serveur

### Enregistrement SD

| Qualité | Débit estimé | Stockage / 24h |
|---|---|---|
| Haute (8) | ~160 KB/s | ~14 Go |
| Normale (12) | ~100 KB/s | ~9 Go |
| Économe (20) | ~50 KB/s | ~4 Go |

- Fichiers horaires : `/rec/YYYY-MM-DD_HH.pcs`
- Format : séquence de `[uint32 taille][JPEG]`
- Purge automatique des fichiers > 24h

### Interface enregistrements (`/recordings`)

- Liste des fichiers par heure
- **Play** : lecture MJPEG directement dans le navigateur
- **Télécharger** : fichier brut

### Librairies requises

Uniquement le **core ESP32 Arduino** (Espressif) — aucune lib externe.
Installer via Arduino IDE : *Boards Manager → ESP32 by Espressif Systems*

---

## Sécurité

- Sessions Flask + CSRF implicite (formulaires POST uniquement)
- Rate limiting via Flask-Limiter (20 req/min sur `/login`)
- Hachage des mots de passe (Werkzeug PBKDF2)
- API Keys par caméra — aucune donnée sensible en clair
- Pas de traversal de chemin sur le serveur de fragments SD

---

## Reconnaissance de plaques (OCR)

1. Créer un compte sur [platerecognizer.com](https://platerecognizer.com)
2. Dashboard PCS → **OCR Configuration** → coller le token
3. Le système analyse automatiquement chaque frame et alerte si la plaque figure dans la blacklist

---

## Conditions d'utilisation

L'utilisation de PCS est soumise aux [Conditions d'utilisation](/terms) disponibles sur la plateforme.
Les développeurs déclinent toute responsabilité pour toute utilisation illicite ou non conforme aux lois applicables en matière de vidéosurveillance et de protection des données.

---

## Licence

ParkingCamSecurity — Open Source
© 2024 — [contact@parkingcamsecurity.com](mailto:contact@parkingcamsecurity.com)
