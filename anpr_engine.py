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

        # Cache du client Roboflow — instancié UNE seule fois par clé API
        # (clé → InferenceHTTPClient) pour éviter la reconnexion TLS à chaque frame
        self._rf_clients = {}

        logger.info("[ANPR] Engine ready.")

    # COCO class id → readable vehicle type
    _VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

    def _detect_color(self, crop):
        """Détecte la couleur dominante d'un crop véhicule (BGR → HSV)."""
        import cv2

        if crop is None or crop.size == 0:
            return "unknown"
        try:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
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
            try:
                ocr_results = self._ocr.readtext(crop, detail=1)
            except Exception as e:
                logger.warning(f"[ANPR] OCR error: {e}")
                ocr_results = []

            for _, text, conf in ocr_results:
                if conf < 0.3:
                    continue
                normalized = normalize_plate(text)
                if len(normalized) < 4 or len(normalized) > 12:
                    continue
                has_digit = any(c.isdigit() for c in normalized)
                has_alpha = any(c.isalpha() for c in normalized)
                if not (has_digit and has_alpha):
                    continue
                if conf > best_conf:
                    plate_found = normalized
                    best_conf = conf

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

        # Étape 3 : Intégration Roboflow (Modèle Expert Custom) via InferenceHTTPClient
        # ⚠️  Appelé UNIQUEMENT si YOLO a trouvé au moins un vrai véhicule.
        #     Si YOLO ne voit rien, inutile d'interroger Roboflow (économie ~90% appels).
        if roboflow_key and roboflow_model and yolo_found_vehicles:
            try:
                from inference_sdk import InferenceHTTPClient

                # ── Client mis en cache par clé API (évite reconnexion TLS à chaque frame) ──
                if roboflow_key not in self._rf_clients:
                    logger.info("[ANPR] Initializing Roboflow client (first use)")
                    new_client = InferenceHTTPClient(
                        api_url="https://detect.roboflow.com",
                        api_key=roboflow_key,
                    )
                    # Timeout explicite sur la session requests sous-jacente
                    # pour ne pas bloquer le tpool thread si Railway a des problèmes DNS
                    try:
                        new_client._session.request = lambda method, url, **kwargs: \
                            type(new_client._session).request(
                                new_client._session, method, url,
                                timeout=kwargs.pop("timeout", 5), **kwargs
                            )
                    except Exception:
                        pass  # Si l'SDK change d'interface, on continue sans timeout forcé
                    self._rf_clients[roboflow_key] = new_client
                client = self._rf_clients[roboflow_key]

                # img est déjà décodé plus haut — pas besoin de re-décoder les bytes
                rf_res = client.infer(img, model_id=roboflow_model)

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
                # Invalidate cached client si erreur réseau persistante
                self._rf_clients.pop(roboflow_key, None)

        if any(d["plate"] for d in final):
            logger.info(
                f"[ANPR] Detected plates: {[d['plate'] for d in final if d['plate']]}"
            )

        return final
