"""
PCS — ANPR Engine (Singleton)
Détection de plaques d'immatriculation via YOLOv11-nano + EasyOCR.

Chargé UNE SEULE FOIS au démarrage de l'application.
Toute inference doit passer par eventlet.tpool.execute() pour éviter
les deadlocks entre eventlet (monkey-patching) et torch (threads natifs).

Usage:
    import eventlet
    from anpr_engine import ANPREngine

    engine = ANPREngine.get_instance()
    results = eventlet.tpool.execute(engine.detect_plates, image_bytes)
    # results = [{'plate': 'AB123CD', 'confidence': 0.87, 'bbox': [x1,y1,x2,y2]}]
"""

import re
import logging
import eventlet
import eventlet.tpool
import numpy as np

logger = logging.getLogger(__name__)


def normalize_plate(plate_str):
    """Normalise une plaque : supprime caractères non-alphanumériques, uppercase."""
    if not plate_str:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(plate_str).upper())


class ANPREngine:
    """Singleton — charge YOLOv11-nano + EasyOCR une seule fois."""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if ANPREngine._instance is not None:
            raise RuntimeError("Use ANPREngine.get_instance()")

        logger.info("[ANPR] Loading YOLOv11-nano model...")
        from ultralytics import YOLO

        self._yolo = YOLO("yolo11n.pt")
        logger.info("[ANPR] YOLOv11-nano loaded.")

        logger.info("[ANPR] Loading EasyOCR (English, cpu)...")
        import easyocr

        self._ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
        logger.info("[ANPR] EasyOCR loaded.")

        logger.info("[ANPR] Engine ready.")

    # COCO class id → readable vehicle type
    _VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

    def _detect_color(self, crop):
        """
        Détecte la couleur dominante d'un crop véhicule (BGR → HSV).

        N'utilise que la zone CENTRALE du crop (20-80% h, 15-85% w) pour
        éviter que l'arrière-plan (herbe, ciel, bitume) fausse le résultat.
        """
        import cv2

        if crop is None or crop.size == 0:
            return "unknown"
        try:
            h_full, w_full = crop.shape[:2]
            # Recadrer sur la zone centrale = carrosserie, pas le fond
            y1 = int(h_full * 0.20)
            y2 = int(h_full * 0.80)
            x1 = int(w_full * 0.15)
            x2 = int(w_full * 0.85)
            center = crop[y1:y2, x1:x2]
            if center.size == 0:
                center = crop

            hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
            v = hsv[:, :, 2]
            s = hsv[:, :, 1]
            mean_v = float(np.mean(v))
            mean_s = float(np.mean(s))
            if mean_v < 50:
                return "black"
            if mean_v > 200 and mean_s < 50:
                return "white"
            if mean_v > 120 and mean_s < 60:
                return "silver"
            mask = s > 50
            if not np.any(mask):
                return "silver"
            hues = hsv[:, :, 0][mask]
            h = float(np.mean(hues))
            if h < 15 or h >= 165:
                return "red"
            if h < 30:
                return "orange"
            if h < 45:
                return "yellow"
            if h < 85:
                return "green"
            if h < 130:
                return "blue"
            return "purple"
        except Exception:
            return "unknown"

    def _compute_iou(self, boxA, boxB):
        if not boxA or not boxB or len(boxA) != 4 or len(boxB) != 4:
            return 0.0
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, float(xB - xA)) * max(0, float(yB - yA))
        boxAArea = float(boxA[2] - boxA[0]) * float(boxA[3] - boxA[1])
        boxBArea = float(boxB[2] - boxB[0]) * float(boxB[3] - boxB[1])
        denom = boxAArea + boxBArea - interArea
        if denom <= 0:
            return 0.0
        return interArea / denom

    def _find_plate_crop(self, vehicle_crop):
        """
        Cherche la région plaque dans un crop véhicule.

        Exploite le fait que les plaques FR/EU ont un fond BLANC/GRIS CLAIR
        avec des caractères NOIRS. On cherche un rectangle blanc dans le
        tiers inférieur du véhicule (là où se trouve la plaque avant/arrière).

        Retourne le crop plaque recadré, ou None si non trouvé.
        """
        import cv2

        h, w = vehicle_crop.shape[:2]
        if h < 30 or w < 60:
            return None

        # Chercher dans le tiers inférieur du véhicule (plaque avant/arrière)
        y_start = int(h * 0.5)
        search = vehicle_crop[y_start:, :]
        sh, sw = search.shape[:2]

        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

        # Binarisation : pixels clairs (fond plaque blanc/gris > 150)
        _, white_mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        # Fermeture morphologique pour remplir les trous créés par les caractères noirs
        # Kernel horizontal large pour connecter les lettres entre elles
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 6))
        closed = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        best_crop = None
        best_score = 0.0

        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 50 or ch < 10:
                continue

            aspect = cw / max(ch, 1)
            # Plaque FR : 520×110mm → ratio ≈ 4.7
            # Plaque EU moto : plus court (~3.0), camion (~5.5)
            if not (2.5 <= aspect <= 7.0):
                continue

            area = cw * ch
            # Doit représenter au moins 3% de la zone de recherche
            if area < (sw * sh * 0.03):
                continue

            # Score : récompenser le ratio proche de 4.7 et la grande surface
            ratio_score = 1.0 / (abs(aspect - 4.7) + 0.5)
            score = ratio_score * area
            if score > best_score:
                best_score = score
                # Petit padding autour du crop plaque pour l'OCR
                pad = 4
                x1p = max(0, x - pad)
                y1p = max(0, y - pad)
                x2p = min(sw, x + cw + pad)
                y2p = min(sh, y + ch + pad)
                best_crop = search[y1p:y2p, x1p:x2p]

        return best_crop

    def _find_all_plate_crops(self, img):
        """
        Cherche TOUTES les régions plaque dans l'image entière.
        Contrairement à _find_plate_crop() qui ne cherche que dans le bas
        d'un crop véhicule, celle-ci scanne toute l'image pour détecter
        des plaques indépendamment de la détection YOLO.
        Retourne une liste de crops candidats (max 5).
        """
        import cv2

        h, w = img.shape[:2]
        if h < 30 or w < 60:
            return []

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, white_mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 6))
        closed = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        crops = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 50 or ch < 10:
                continue
            aspect = cw / max(ch, 1)
            if not (2.5 <= aspect <= 7.0):
                continue
            area = cw * ch
            if area < (w * h * 0.002):
                continue
            pad = 4
            crop = img[max(0, y - pad):min(h, y + ch + pad), max(0, x - pad):min(w, x + cw + pad)]
            if crop.size > 0:
                crops.append(crop)

        return crops[:5]

    def _call_roboflow(self, api_key, model_id, image_bytes, timeout=6):
        """
        Appelle l'API Roboflow Serverless via curl (subprocess).

        Pourquoi curl et pas urllib/requests ?
        eventlet.monkey_patch() remplace socket.getaddrinfo ET socket.create_connection
        au niveau du module Python. Même dans un thread tpool, les deux sont patchés
        et échouent sur Railway ([Errno -3] Lookup timed out).
        curl est un processus OS séparé → complètement indépendant de Python/eventlet.

        Équivalent de :
          base64 < image.jpg | curl -d @- \
            "https://serverless.roboflow.com/model/1?api_key=KEY"

        Retourne le dict JSON Roboflow ou None en cas d'erreur.
        """
        import base64
        import json
        import subprocess

        img_b64 = base64.b64encode(image_bytes).decode("ascii")
        url = f"https://serverless.roboflow.com/{model_id}?api_key={api_key}"

        try:
            result = subprocess.run(
                [
                    "curl", "-s",
                    "-m", str(timeout),
                    "-X", "POST",
                    "-H", "Content-Type: text/plain",
                    "--data-binary", "@-",
                    url,
                ],
                input=img_b64,
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )
        except FileNotFoundError:
            logger.error("[ANPR] curl not found — install curl in Dockerfile")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("[ANPR] Roboflow curl timeout")
            return None

        if result.returncode != 0 or not result.stdout:
            logger.warning(f"[ANPR] Roboflow curl failed (rc={result.returncode}): {result.stderr[:200]}")
            return None

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.warning(f"[ANPR] Roboflow invalid JSON: {e} — stdout: {result.stdout[:200]}")
            return None

    def detect_plates(self, image_bytes, roboflow_key=None, roboflow_model=None):
        """
        Détecte les véhicules et plaques dans une image JPEG brute.

        Retourne une liste de détections, une par véhicule trouvé.
        Chaque détection contient : plate (peut être None), vehicle_type, vehicle_color.

        Returns:
            list[dict] — [{
                'plate': 'AB123CD' or None,
                'confidence': 0.8,
                'bbox': [x1,y1,x2,y2],
                'vehicle_type': 'car',
                'vehicle_color': 'red',
            }]
        """
        import cv2

        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("[ANPR] Failed to decode image")
            return []

        h, w = img.shape[:2]
        detections = []

        # Étape 1 : YOLO — détecter les véhicules
        # Augmentation du seuil de confiance (0.45 au lieu de 0.3) pour réduire la "nervosité" du modèle nano
        # Ajout de l'IoU (0.5) pour le Non-Maximum Suppression (réduit les détections multiples sur un même objet)
        results = self._yolo(img, verbose=False, conf=0.45, iou=0.5)

        vehicle_crops = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in self._VEHICLE_CLASSES:
                    continue
                vehicle_type = self._VEHICLE_CLASSES[cls_id]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                pad_y = int((y2 - y1) * 0.1)
                pad_x = int((x2 - x1) * 0.05)
                x1c = max(0, x1 - pad_x)
                y1c = max(0, y1 - pad_y)
                x2c = min(w, x2 + pad_x)
                y2c = min(h, y2 + pad_y)
                crop = img[y1c:y2c, x1c:x2c]
                if crop.size > 0:
                    color = self._detect_color(crop)
                    vehicle_crops.append(
                        (
                            crop,
                            [x1c, y1c, x2c, y2c],
                            float(box.conf[0]),
                            vehicle_type,
                            color,
                        )
                    )

        # Indique si YOLO a trouvé de vrais véhicules (avant le fallback)
        yolo_found_vehicles = len(vehicle_crops) > 0

        # Fallback : image entière si aucun véhicule
        if not vehicle_crops:
            color = self._detect_color(img)
            vehicle_crops = [(img, [0, 0, w, h], 0.0, "unknown", color)]

        # Étape 2 : EasyOCR — lire le texte dans chaque crop
        for crop, bbox, veh_conf, vehicle_type, vehicle_color in vehicle_crops:
            plate_found = None
            best_conf = 0.0

            # Tentative 1 : OCR sur le crop plaque isolé (fond blanc, aspect ratio plaque)
            # → plus précis car EasyOCR ne se perd pas dans la carrosserie
            plate_crop = self._find_plate_crop(crop)
            ocr_targets = []
            if plate_crop is not None and plate_crop.size > 0:
                ocr_targets.append(("plate_crop", plate_crop))
            # Tentative 2 : OCR sur le crop véhicule entier (fallback)
            ocr_targets.append(("full_crop", crop))

            for target_name, target_img in ocr_targets:
                if plate_found:
                    break  # déjà trouvé sur le crop plaque, on s'arrête
                try:
                    # paragraph=False → EasyOCR renvoie chaque mot séparément
                    # (utile pour reconstituer "82 EAH 78" depuis 3 blocs)
                    ocr_results = self._ocr.readtext(
                        target_img, detail=1, paragraph=False
                    )
                except Exception as e:
                    logger.warning(f"[ANPR] OCR error ({target_name}): {e}")
                    ocr_results = []

                # Seuil de confiance plus bas sur le crop plaque (on sait que c'est une plaque)
                conf_threshold = 0.2 if target_name == "plate_crop" else 0.3

                # Collecter TOUS les fragments alphanumériques triés gauche→droite
                # EasyOCR renvoie (bbox_points, text, conf) — bbox_points[0] = coin haut-gauche
                fragments = []
                for bbox_pts, text, conf in ocr_results:
                    if conf < conf_threshold:
                        continue
                    normalized = normalize_plate(text)
                    if len(normalized) < 2:   # fragment minimum 2 chars
                        continue
                    if not any(c.isdigit() or c.isalpha() for c in normalized):
                        continue
                    # Position X du coin haut-gauche pour trier gauche→droite
                    x_pos = bbox_pts[0][0] if bbox_pts else 0
                    fragments.append((x_pos, normalized, conf))

                if not fragments:
                    continue

                # Trier par position X (gauche → droite)
                fragments.sort(key=lambda f: f[0])

                # Cas 1 : un seul fragment ≥ 4 chars avec chiffre+lettre → plaque directe
                for x_pos, frag, conf in fragments:
                    if (len(frag) >= 4
                            and any(c.isdigit() for c in frag)
                            and any(c.isalpha() for c in frag)):
                        if conf > best_conf:
                            plate_found = frag
                            best_conf = conf

                # Cas 2 : concaténer tous les fragments pour reconstituer la plaque entière
                # ex: ["82", "EAH", "78"] → "82EAH78"
                if len(fragments) >= 2:
                    combined = "".join(f[1] for f in fragments)
                    if (4 <= len(combined) <= 12
                            and any(c.isdigit() for c in combined)
                            and any(c.isalpha() for c in combined)):
                        avg_conf = sum(f[2] for f in fragments) / len(fragments)
                        # Préférer la version combinée si elle est plus longue
                        if plate_found is None or len(combined) > len(plate_found):
                            plate_found = combined
                            best_conf = avg_conf

            detections.append(
                {
                    "plate": plate_found,
                    "confidence": round(best_conf, 3)
                    if plate_found
                    else round(veh_conf, 3),
                    "bbox": bbox,
                    "vehicle_type": vehicle_type,
                    "vehicle_color": vehicle_color,
                }
            )

        # Dédupliquer les plaques (garder meilleure confiance)
        seen_plates = {}
        final = []
        for d in detections:
            if d["plate"]:
                p = d["plate"]
                if (
                    p not in seen_plates
                    or d["confidence"] > seen_plates[p]["confidence"]
                ):
                    seen_plates[p] = d
            else:
                # Si pas de plaque, on ne garde que s'il y a un VRAI véhicule détecté (pas le fallback "unknown")
                if d["vehicle_type"] != "unknown":
                    final.append(d)

        final.extend(seen_plates.values())

        # Étape 3 : Intégration Roboflow Serverless (urllib + base64, pas inference_sdk)
        # ⚠️  Appelé UNIQUEMENT si YOLO a trouvé au moins un vrai véhicule.
        if roboflow_key and roboflow_model and yolo_found_vehicles:
            try:
                rf_res = self._call_roboflow(roboflow_key, roboflow_model, image_bytes)

                if rf_res and "predictions" in rf_res:
                    predictions = rf_res.get("predictions", [])
                    for p in predictions:
                        rf_conf = p.get("confidence", 0)
                        if rf_conf < 0.4:
                            continue

                        rf_class = p.get("class", "expert_detection").lower()

                        # Roboflow renvoie centre+taille → convertir en x1,y1,x2,y2
                        w_half = p["width"] / 2
                        h_half = p["height"] / 2
                        x1 = int(p["x"] - w_half)
                        y1 = int(p["y"] - h_half)
                        x2 = int(p["x"] + w_half)
                        y2 = int(p["y"] + h_half)
                        rf_bbox = [x1, y1, x2, y2]

                        # Fusion IoU avec les détections YOLO existantes
                        matched = False
                        for d in final:
                            iou = self._compute_iou(rf_bbox, d.get("bbox"))
                            if iou > 0.4:
                                # Roboflow enrichit le type de véhicule (modèle expert)
                                d["vehicle_type"] = rf_class
                                if rf_conf > d["confidence"]:
                                    d["confidence"] = round(rf_conf, 3)
                                matched = True
                                break

                        # Roboflow a trouvé un véhicule que YOLO a raté → on l'ajoute
                        if not matched:
                            crop_rf = img[
                                max(0, y1):min(h, y2),
                                max(0, x1):min(w, x2)
                            ]
                            color = self._detect_color(crop_rf)
                            final.append(
                                {
                                    "plate": None,
                                    "confidence": round(rf_conf, 3),
                                    "bbox": rf_bbox,
                                    "vehicle_type": rf_class,
                                    "vehicle_color": color,
                                }
                            )

            except Exception as e:
                logger.error(f"[ANPR] Roboflow error: {e}")

        # Étape 4 : Scan plaque plein écran (indépendant de YOLO)
        # Cherche des rectangles blanc/plaque PARTOUT dans l'image
        already_found_plates = {d["plate"] for d in final if d["plate"]}
        full_plate_crops = self._find_all_plate_crops(img)
        for plate_crop in full_plate_crops:
            try:
                ocr_results = self._ocr.readtext(plate_crop, detail=1, paragraph=False)
            except Exception:
                continue

            fragments = []
            for bbox_pts, text_val, conf in ocr_results:
                if conf < 0.2:
                    continue
                normalized = normalize_plate(text_val)
                if len(normalized) < 2:
                    continue
                x_pos = bbox_pts[0][0] if bbox_pts else 0
                fragments.append((x_pos, normalized, conf))

            if not fragments:
                continue

            fragments.sort(key=lambda f: f[0])
            combined = "".join(f[1] for f in fragments)

            if (4 <= len(combined) <= 12
                    and any(c.isdigit() for c in combined)
                    and any(c.isalpha() for c in combined)
                    and combined not in already_found_plates):
                avg_conf = sum(f[2] for f in fragments) / len(fragments)
                final.append({
                    "plate": combined,
                    "confidence": round(avg_conf, 3),
                    "bbox": [0, 0, w, h],
                    "vehicle_type": "unknown",
                    "vehicle_color": "unknown",
                })
                already_found_plates.add(combined)

        if any(d["plate"] for d in final):
            logger.info(
                f"[ANPR] Detected plates: {[d['plate'] for d in final if d['plate']]}"
            )

        return final
