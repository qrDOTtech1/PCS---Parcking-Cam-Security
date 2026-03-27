# Guide Déploiement Railway (Sans Git)

## 1. Créer le compte

1. Va sur **railway.app**
2. Clique **"Sign Up"** (Google ou GitHub)
3. Crée un nouveau projet vide

## 2. Préparer les fichiers

Assure-toi d'avoir ces fichiers dans un dossier :
```
parkingcam/
├── app.py
├── models.py
├── requirements.txt
├── templates/
│   ├── login.html
│   ├── dashboard.html
│   └── camera_view.html
```

## 3. Uploader sur Railway

### Via le dashboard (pas de Git)

1. Dans Railway, clique **"New"** → **"Empty Service"**
2. Clique sur le nom du service → **"Settings"**
3. Descends jusqu'à **"Deploy"**
4. Clique **"Upload ZIP"**
5. Sélectionne ton dossier compressé en ZIP

## 4. Configuration

Une fois déployé, configure les variables d'environnement :

1. Dans Railway Dashboard → ton service → **"Variables"**
2. Ajoute :
   ```
   DATABASE_URL=sqlite:///parkingcam.db
   SECRET_KEY=une_cle_secrete_unique
   PORT=5000
   ```

## 5. Configuration du démarrage

1. Dans Railway → **"Settings"** → **"Start Command"**
2. Mets :
   ```
   gunicorn --bind 0.0.0.0:$PORT --workers 2 --worker-class eventlet app:app
   ```

## 6. Tester

- L'URL sera : `https://ton-projet.up.railway.app`
- Premier déploiement peut prendre 2-3 minutes

---

## Notes

- **Gratuit** : 500h/mois (suffisant pour test)
- **Limite** : free tier met en veille après ~5min d'inactivité (cold start)