# СЛЕДИ (SLEDI) - Server v2.0

## Installation sur PythonAnywhere

### 1. Préparer l'environnement

```bash
pip install -r requirements.txt
```

### 2. Configuration

Créez un fichier `.env` ou définissez les variables d'environnement :
```bash
export SECRET_KEY="votre-cle-secrete-tres-longue"
export ADMIN_MASTER_KEY="votre-master-key-pour-admin"
export PLATE_RECOGNIZER_URL="https://api.platerecognizer.com/v1/plate-reader"
```

### 3. Lancer le serveur

```bash
python app.py
```

Ou via WSGI :
```bash
python vigilance_wsgi.py
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

### API Camera (config)
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

SQLite : `vigilance_multi.db`

### Tables

- **User**: Utilisateurs (auth, subscription)
- **Camera**: Caméras (name, api_key, config_json, etc.)
- **Blacklist**: Plaques suspectes
- **NotificationTarget**: Canaux Signal/Telegram
- **SystemConfig**: Configs système (Plate Recognizer token)

### Migrations

Les migrations sont automatiques au premier lancement.

## Migration vers un autre serveur

Le code est 100% portable :

1. Copiez tous les fichiers du dossier `SERV/`
2. Exécutez `pip install -r requirements.txt`
3. Configurez les variables d'environnement
4. Lancez `python app.py`

La base SQLite peut être copiée simplement sur le nouveau serveur.

## Structure des fichiers

```
SERV/
├── app.py              # Application Flask principale
├── models.py           # Modèles SQLAlchemy
├── requirements.txt    # Dépendances Python
├── admin_tools.py     # Outil admin desktop
├── webcam_tester.py   # Simulateur pour test
├── templates/
│   ├── login.html     # Page login
│   ├── dashboard.html # Vue grille caméras
│   └── camera_view.html # Vue détaillée caméra
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

## Plate Recognizer

Pour activer l'OCR, configurez votre token dans le dashboard :
1. Créez un compte sur https://api.platerecognizer.com
2. Ajoutez votre token dans Configuration AI Engine
3. Le système analysera automatiquement les plaques

## Licence

Projet SLEDI - Vigilance Drive
