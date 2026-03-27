# SLEDI - Système de Surveillance Véhicules

## Vue d'ensemble

SLEDI est un système de surveillance en temps réel pour lesloueurs de véhicules. Il permet de :
- 📹 Visualiser le flux vidéo des véhicules en temps réel
- 🚗 Détecter automatiquement les plaques d'immatriculation
- ⚠️ Alerter en cas de véhicule suspect (police, voleur, etc.)
- 📍 Suivre la position GPS des véhicules
- 🔔 Recevoir des notifications (Signal, Telegram)

---

## Matériel requis

### Option 1 : ESP32-CAM (WiFi)
- ESP32-CAM (5-15€)
- Alimentation 5V
- Connexion WiFi disponible

### Option 2 : T-SIMCam (4G/SIM)
- T-SIMCam A7600 (20-40€)
- Carte SIM avec data
- Autonome (pas de WiFi needed)

---

## Installation

### 1. Serveur (Hetzner - ~4.50€/mois)

```bash
# Connexion au serveur
ssh root@IP_DU_SERVEUR

# Installation
apt update && apt install python3 python3-pip python3-venv nginx -y
mkdir -p /var/www/vigilance
cd /var/www/vigilance
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Créer admin
python3 -c "
from app import app, db
from models import User
app.app_context().push()
db.create_all()
u = User(username='admin')
u.set_password('votre_mot_de_passe')
db.session.add(u)
db.session.commit()
"
```

### 2. Configuration Nginx

```bash
nano /etc/nginx/sites-available/vigilance
# Coller la config nginx (voir DEPLOY.md)
ln -s /etc/nginx/sites-available/vigilance /etc/nginx/sites-enabled/
systemctl reload nginx
```

### 3. Service systemd

```bash
nano /etc/systemd/system/vigilance.service
# Coller le service (voir DEPLOY.md)
systemctl enable vigilance
systemctl start vigilance
```

---

## Configuration des caméras

### Page de configuration (hotspot)

1. Allumer la caméra → hotspot `SLEDI_CONFIG` apparaît
2. Se connecter au WiFi `SLEDI_CONFIG` (mdp: 12345678)
3. Ouvrir `http://192.168.4.1`

**Paramètres :**

| Champ | Description |
|-------|-------------|
| Mode de connexion | **WiFi** ou **SIM** |
| URL Serveur | `http://IP_DU_SERVEUR` |
| API Key | Clé unique (ex: `cam_001`) |
| SSID | Nom du réseau WiFi |
| Mot de passe | Mot de passe WiFi |
| Réseau masqué | Cocher si SSID caché |
| Mode Stream | OFF / Motion / Boucle |
| FPS Boucle | Images par seconde (5-30) |

---

## Modes de fonctionnement

### Mode OFF
- Pas d'envoi automatique
- Utilisation manuelle (bouton capture)

### Mode Motion (Mouvement)
- Envoie une image quand un mouvement est détecté
- Seuil de détection paramétrable (3-50%)

### Mode Boucle (Loop)
- Envoie des images en continu à intervalle régulier
- Idéal pour suivi temps réel

---

## Tableau de bord

Accéder à `http://IP_DU_SERVEUR`

### Dashboard
- Liste des caméras
- Statut (online/offline)
- Dernière connexion

### Vue caméra
- Flux vidéo temps réel
- Configuration (FPS, qualité, mode)
- Alertes en direct

### Gestion
- Ajouter/supprimer des caméras
- Liste noire (plaques suspectes)
- Notifications (Signal/Telegram)

---

## API pour intégrations

### Upload d'image
```
POST /upload
Headers: X-API-Key: cam_001
Body: image (fichier JPEG)
```

### Ping / Configuration
```
GET /ping?api_key=cam_001
```

### Upload stream
```
POST /stream_upload
Headers: X-API-Key: cam_001
Body: image + lat/lng/speed
```

---

## Coûts

| Élément | Coût |
|---------|------|
| Serveur Hetzner CPX11 | 4.50€/mois |
| ESP32-CAM | 5-15€ (une fois) |
| T-SIMCam | 20-40€ (une fois) |
| Carte SIM data | ~5€/mois |
| **Total par véhicule** | **~10-15€** |

---

## Dépannage

### La caméra n'apparaît pas online
- Vérifier la connexion WiFi/SIM
- Vérifier l'URL du serveur
- Vérifier la clé API

### Pas d'images
- Vérifier le mode stream (doit être "loop" ou "motion")
- Vérifier les logs serveur: `journalctl -u vigilance -f`

### FPS trop bas
- Réduire la qualité JPEG
- Réduire les FPS
- Vérifier la connexion internet

---

## Sécurité

- ✅ API Key par caméra
- ✅ Session utilisateur
- ✅ HTTPS avec Certbot (optionnel)
- ⚠️ Changer le SECRET_KEY en prod

---

## Support

Pour toute question : [votre email]
