# ParkingCamSecurity (PCS) - Server v2.0

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copie `.env.example` en `.env` et remplis les valeurs :
```bash
FLASK_SECRET_KEY=une-cle-secrete-longue-et-unique
MASTER_KEY=ta-master-key-admin
DATABASE_URL=sqlite:///parkingcam.db
SERVER_URL=https://ton-app.railway.app
```

## Lancer le serveur

```bash
python app.py
```

## Endpoints API

### Authentification
| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/login` | GET/POST | Connexion utilisateur |
| `/logout` | GET | Déconnexion |
| `/dashboard` | GET | Interface utilisateur |

### Caméra ESP32
| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/ping` | GET | Heartbeat + reception config |
| `/stream_upload` | POST | Reception frames continues |
| `/upload` | POST | Analyse OCR d'une image |

### API Caméra (config)
| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/camera/config/<key>` | GET | Lire config caméra |
| `/api/camera/config/<key>` | POST | Écrire config caméra |
| `/api/camera/recording/<key>` | POST | Start/Stop recording |
| `/api/camera/fragments/<key>` | GET | Liste fragments vidéo |
| `/api/camera/download/<key>` | GET | Demander download 15min |

### WebSocket Events
| Event | Direction | Description |
|-------|-----------|-------------|
| `esp32_register` | ESP32 → Server | Inscription ESP32 |
| `client_watch` | Client → Server | Client regarde stream |
| `config_update` | Server → ESP32 | Push nouvelle config |
| `threat_alert` | Server → Client | Alerte plaque |

## Base de données

SQLite : `parkingcam.db`

### Tables

- **User** : Utilisateurs (auth, subscription)
- **Camera** : Caméras (name, api_key, config_json, etc.)
- **Blacklist** : Plaques suspectes
- **NotificationTarget** : Canaux Signal/Telegram
- **SystemConfig** : Configs système (Plate Recognizer token)

## Structure des fichiers

```
ParkingCamSecurity/
├── app.py              # Application Flask principale
├── models.py           # Modèles SQLAlchemy
├── requirements.txt    # Dépendances Python
├── .env.example        # Template de configuration
├── Dockerfile          # Déploiement Docker
├── admin_tools.py      # Outil admin desktop
├── webcam_tester.py    # Simulateur ESP32 pour test
├── stream_tester.py    # Test de flux vidéo
├── esp32_tracker.ino   # Firmware ESP32
├── templates/
│   ├── login.html      # Page login
│   ├── dashboard.html  # Vue grille caméras
│   └── camera_view.html # Vue détaillée caméra
├── deploy/
│   ├── hetzner/        # Guide déploiement Hetzner
│   └── railway/        # Guide déploiement Railway
└── static/
    ├── css/
    └── js/
```

## WebSocket (Socket.IO)

Le serveur utilise Flask-SocketIO pour les communications temps réel :
- Push config vers ESP32
- Alertes vers dashboard
- Mise à jour frame en temps réel

## Sécurité

- API Keys uniques par caméra
- Sessions Flask pour utilisateurs
- Rate limiting (Flask-Limiter)
- Password hashing (Werkzeug)

## Plate Recognizer (OCR)

Pour activer la reconnaissance de plaques :
1. Crée un compte sur https://api.platerecognizer.com
2. Ajoute ton token dans Configuration AI Engine du dashboard
3. Le système analysera automatiquement les plaques détectées

## Déploiement

Voir `deploy/railway/DEPLOY.md` pour Railway ou `deploy/hetzner/DEPLOY.md` pour Hetzner.

## Licence

ParkingCamSecurity (PCS) - Open Source
