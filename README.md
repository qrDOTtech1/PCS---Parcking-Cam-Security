# NovaSecurity

**Système de vidéosurveillance intelligent — Webcam, smartphone ou caméra dédiée NovaCam.**

> Stream live · Reconnaissance de plaques (OCR) · Alertes intelligentes · Multi-usage (Parking, Bureau, Maison, Dashcam)

---

## Aperçu

NovaSecurity est une plateforme web Flask centralisant vos caméras de surveillance dans un seul dashboard intelligent.

Compatible avec les webcam, smartphones, ou le matériel NovaCam dédié. **Non compatible avec les caméras IP génériques** — pour cela, commandez un devis NovaCam custom via le chatbot NovaVivo.

---

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| **Dashboard live** | Vignettes toutes caméras, polling 500 ms |
| **Camera view** | Flux MJPEG continu (vrai stream, pas de polling) |
| **Camera setup** | Page `/setup` — utiliser téléphone/webcam sans connexion |
| **Reconnaissance plaques** | Intégration Plate Recognizer API (OCR) |
| **Intelligence Roboflow** | Détection multi-modèles personnalisés (Pro+) |
| **Blacklist / Whitelist** | Alertes temps réel via Socket.IO |
| **Carte GPS** | Positionnement caméras sur carte Leaflet |
| **Détection gyrophares** | Reconnaissance véhicules d'urgence |
| **GIF alertes** | Enregistrement GIF automatique des passages suspects |
| **Résumés IA** | Résumés périodiques intelligents (Pro+) |

---

## Matériel Compatible

| Source | Statut | Utilisation |
|---|---|---|
| **Webcam PC / Mac** | ✅ Supporté | Via navigateur (`/setup`) |
| **Smartphone (iOS/Android)** | ✅ Supporté | Via navigateur (`/setup`) |
| **NovaCam (Matériel dédié)** | ✅ Supporté | Firmware ESP32-CAM fourni |
| **Caméras IP génériques** | ❌ Non supporté | Demandez un devis custom |

> **Besoin d'une caméra IP intégrée ?** Contactez-nous via le chatbot NovaVivo pour un devis matériel personnalisé (NovaCam PoE, WiFi, extérieur, etc.)

---

## Démarrage rapide

### 1. Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `FLASK_SECRET_KEY` | Clé secrète Flask | (requis en production) |
| `MASTER_KEY` | Clé admin pour créer des comptes | `master_key_pcs_2024` |
| `DATABASE_URL` | URI SQLAlchemy | `sqlite:///novasecurity.db` |
| `NOVA_ADMIN_URL` | URL du back-office NovaAdmin | `https://novaxadmin.casa` |
| `INTERNAL_API_KEY` | Clé interne commune à l'écosystème | (obligatoire) |

### 2. Lancer en local

```bash
pip install -r requirements.txt
python app.py
```

### 3. Déployer sur Railway

1. Push ce repo sur GitHub
2. Nouveau projet Railway → *Deploy from GitHub repo*
3. Ajouter les variables d'environnement ci-dessus
4. *(Optionnel)* Ajouter un **Volume** monté sur `/app/data` pour persister la DB

---

## Ajouter un appareil

### Via Webcam / Smartphone (Navigateur)

1. Ouvrir `https://<votre-serveur>/setup`
2. Choisir la caméra du device, définir un nom
3. Cliquer **Start streaming**
4. Dashboard → **Ajouter un appareil** → saisir le même ID

### Via NovaCam (Matériel ESP32)

Flasher `esp32cam/NovaSecurity_ESP32.ino` via Arduino IDE.
Au premier démarrage (ou GPIO 13 appuyé) → AP WiFi → config SSID, Camera ID, URL serveur.

---

## Structure du projet

```
ParkingCamSecurity/
├── app.py                  # Application Flask + Socket.IO
├── models.py               # Modèles SQLAlchemy
├── requirements.txt        # Dépendances Python
├── Procfile                # gunicorn eventlet
├── Dockerfile              # Image Docker
├── esp32cam/               # Firmware NovaCam
├── templates/              # Templates Jinja2
├── static/                 # Assets (CSS, img, cache)
├── webcam_tester.py        # Simulateur webcam
└── admin_tools.py          # Outils admin
```

---

## Conditions d'utilisation

L'utilisation de NovaSecurity est soumise aux [Conditions d'utilisation](/terms) disponibles sur la plateforme.

---

## NovaVivo — Écosystème

NovaSecurity fait partie de l'écosystème **NovaVivo**, créé par **Steven Franco** (ingénieur IA, +5 ans d'expérience).

| Application | Rôle |
|---|---|
| **NovaSecurity** | Vidéosurveillance intelligente |
| **NovaFact** | Fact-checking live en débats |
| **NovaContab** | Facturation & gestion commerciale |
| **NovaBets** | Prédictions sportives IA |
| **NovaNews** | Moteur d'ingestion d'actualités |

---

NovaSecurity · NovaVivo &copy; 2026
