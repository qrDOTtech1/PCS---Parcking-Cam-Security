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

    def detect_plates(self, image_bytes):
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

        if any(d["plate"] for d in final):
            logger.info(
                f"[ANPR] Detected plates: {[d['plate'] for d in final if d['plate']]}"
            )

        return final
