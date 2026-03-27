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

    def detect_plates(self, image_bytes):
        """
        Détecte les plaques dans une image JPEG brute.

        Stratégie en 2 étapes :
          1. YOLOv11-nano détecte les véhicules (car, truck, bus, motorcycle)
          2. EasyOCR scanne chaque crop véhicule pour du texte type plaque

        Args:
            image_bytes: bytes JPEG de l'image complète

        Returns:
            list[dict] — [{'plate': 'AB123CD', 'confidence': 0.8, 'bbox': [x1,y1,x2,y2]}]
        """
        import cv2

        # Décoder l'image
        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("[ANPR] Failed to decode image")
            return []

        h, w = img.shape[:2]
        detections = []

        # Étape 1 : YOLO — détecter les véhicules
        # Classes COCO : 2=car, 5=bus, 7=truck, 3=motorcycle
        vehicle_classes = {2, 3, 5, 7}
        results = self._yolo(img, verbose=False, conf=0.3)

        vehicle_crops = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in vehicle_classes:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                # Élargir un peu la bbox pour inclure la plaque (souvent en bas)
                pad_y = int((y2 - y1) * 0.1)
                pad_x = int((x2 - x1) * 0.05)
                x1 = max(0, x1 - pad_x)
                y1 = max(0, y1 - pad_y)
                x2 = min(w, x2 + pad_x)
                y2 = min(h, y2 + pad_y)
                crop = img[y1:y2, x1:x2]
                if crop.size > 0:
                    vehicle_crops.append((crop, [x1, y1, x2, y2], float(box.conf[0])))

        # Si aucun véhicule détecté, tenter OCR sur l'image entière (fallback)
        if not vehicle_crops:
            vehicle_crops = [(img, [0, 0, w, h], 0.0)]

        # Étape 2 : EasyOCR — lire le texte dans chaque crop
        for crop, bbox, veh_conf in vehicle_crops:
            try:
                ocr_results = self._ocr.readtext(crop, detail=1)
            except Exception as e:
                logger.warning(f"[ANPR] OCR error: {e}")
                continue

            for ocr_bbox, text, conf in ocr_results:
                if conf < 0.3:
                    continue
                # Filtrer : une plaque a typiquement 4-10 caractères alphanumériques
                normalized = normalize_plate(text)
                if len(normalized) < 4 or len(normalized) > 12:
                    continue
                # Vérifier qu'il y a au moins 1 chiffre ET 1 lettre (pattern plaque)
                has_digit = any(c.isdigit() for c in normalized)
                has_alpha = any(c.isalpha() for c in normalized)
                if not (has_digit and has_alpha):
                    continue

                detections.append({
                    "plate": normalized,
                    "confidence": round(conf, 3),
                    "bbox": bbox,
                })

        # Dédupliquer : garder la meilleure confiance par plaque unique
        seen = {}
        for d in detections:
            p = d["plate"]
            if p not in seen or d["confidence"] > seen[p]["confidence"]:
                seen[p] = d
        detections = list(seen.values())

        if detections:
            logger.info(f"[ANPR] Detected: {[d['plate'] for d in detections]}")

        return detections
