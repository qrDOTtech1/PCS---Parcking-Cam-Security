# Guide de déploiement Hetzner

## 1. Créer le serveur Hetzner

1. Va sur **hetzner.com/cloud** et connecte-toi
2. Clique **"New Project"** → **"Add Server"**
3. Configure :
   - **Image** : Ubuntu 22.04
   - **Type** : CPX11 (~4.50€/mois)
   - **Location** : Frankfurt ou Nuremberg
   - **SSH Key** : Crée une clé ou utilise un mot de passe
4. Cliquez **"Create & Buy Now"**

## 2. Se connecter au serveur

Ouvre **PowerShell** sur ton PC :

```powershell
ssh root@IP_DU_SERVEUR
```

## 3. Installation des dépendances

```bash
# Mise à jour
apt update && apt upgrade -y

# Python et outils
apt install python3 python3-pip python3-venv nginx git -y

# Création du dossier
mkdir -p /var/www/vigilance
cd /var/www/vigilance
```

## 4. Transférer les fichiers

**Via SCP (dans ton PowerShell local) :**

```powershell
# Transférer tous les fichiers du dossier SERV
scp -r C:\Users\Super\Music\ZIK\board\SERV\* root@IP_SERVEUR:/var/www/vigilance/
```

**Fichiers à inclure :**
- app.py
- models.py
- requirements.txt
- templates/ (dossier)
- vigilance_wsgi.py

## 5. Configurer l'application

```bash
cd /var/www/vigilance

# Créer l'environnement virtuel
python3 -m venv venv
source venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt

# Créer la base de données
python3 -c "from app import app, db; app.app_context().push(); db.create_all()"

# Créer un utilisateur admin
python3 -c "
from app import app, db
from models import User
app.app_context().push()
db.create_all()
u = User(username='admin')
u.set_password('TON_MOT_DE_PASSE')
db.session.add(u)
db.session.commit()
print('Admin créé!')
"
```

## 6. Configurer systemd

```bash
nano /etc/systemd/system/vigilance.service
```

Colle ce contenu (remplace `TON_SECRET_KEY` par une clé unique) :

```ini
[Unit]
Description=Vigilance Flask App
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/vigilance
Environment="PATH=/var/www/vigilance/venv/bin"
Environment="DATABASE_URL=sqlite:////var/www/vigilance/vigilance.db"
Environment="SECRET_KEY=TON_SECRET_KEY_UNIQUE"
ExecStart=/var/www/vigilance/venv/bin/gunicorn --workers 4 --bind 127.0.0.1:5000 --worker-class=eventlet --timeout 120 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Activer le service
systemctl daemon-reload
systemctl enable vigilance
systemctl start vigilance

# Vérifier le statut
systemctl status vigilance
```

## 7. Configurer Nginx

```bash
nano /etc/nginx/sites-available/vigilance
```

Colle (remplace `TON_IP` par ton IP Hetzner) :

```nginx
server {
    listen 80;
    server_name TON_IP;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        proxy_read_timeout 86400;
    }

    location /socket.io {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
# Activer le site
ln -s /etc/nginx/sites-available/vigilance /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

## 8. Tester

Ouvre ton navigateur : `http://IP_DU_SERVEUR`

Identifiants :
- Username : **admin**
- Mot de passe : **TON_MOT_DE_PASSE**

## 9. Mettre à jour l'ESP32

Dans le code Arduino, change l'URL :

```cpp
// Avant
const char* serverUrl = "https://Wlansolo.pythonanywhere.com";

// Après
const char* serverUrl = "http://IP_DU_SERVEUR";
```

## Commandes utiles

```bash
# Voir les logs
journalctl -u vigilance -f

# Redémarrer l'app
systemctl restart vigilance

# Mettre à jour les fichiers
# 1. Transfère les nouveaux fichiers
# 2. Redémarre
systemctl restart vigilance
```

---

**Coût** : ~4.50€/mois
