"""
PCS — ANPR Background Worker
Scanne séquentiellement toutes les caméras actives,
détecte les plaques via ANPREngine, et déclenche les alertes blacklist.
Détecte aussi les gyrophares bleus (véhicules d'urgence) par analyse temporelle.

Tourne comme greenlet eventlet dans le même process que Flask.
"""

import os
import time
import logging
import numpy as np
import eventlet
import eventlet.tpool

logger = logging.getLogger(__name__)

# Intervalle minimum entre 2 scans d'une même caméra (secondes)
ANPR_INTERVAL = int(os.environ.get("ANPR_INTERVAL_SECONDS", "3"))

# Cache pour dédupliquer : (camera_id, plate) -> timestamp
RECENT_DETECTIONS = {}
DEDUP_WINDOW = 60  # secondes — même plaque/même cam ignorée pendant 60s

# Timestamp du dernier scan par caméra
ANPR_LAST_SCAN = {}

# ── Détection gyrophare ─────────────────────────────────────────────────────
# Historique d'intensité bleue par caméra : liste des N dernières mesures
BLUE_HISTORY = {}  # camera_id -> list[float]  (couverture bleue 0..1)
BLUE_HISTORY_SIZE = 8  # nb de frames conservées
BLUE_FLASH_MIN_MEAN = 0.08  # au moins 8% du cadre bleu en moyenne
BLUE_FLASH_MIN_VAR = 0.002  # variance minimale = oscillation (flash vs couleur fixe)
BLUE_FLASH_DEDUP = 30  # cooldown entre 2 alertes gyrophare (secondes)
BLUE_FLASH_LAST = {}  # camera_id -> timestamp dernière alerte

# ── Tracking de véhicules (IoU) ─────────────────────────────────────────────
# Permet de vérifier les frames précédentes pour ne pas compter en double
CAMERA_TRACKS = {}  # cam_id -> list of track_data
TRACK_ID_COUNTER = {}  # cam_id -> int
TRACK_EXPIRY_SECONDS = 30  # Si un véhicule disparaît pendant 30s, on l'oublie


def compute_iou(boxA, boxB):
    """Calcule l'Intersection sur Union (IoU) entre deux bounding boxes."""
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


def _measure_blue_coverage(image_bytes):
    """
    Mesure la proportion de pixels "bleu gyrophare" dans l'image.
    Bleu gyrophare : teinte 100-130° (HSV OpenCV 0-180), sat>80, val>80.
    Retourne une valeur entre 0 et 1.
    """
    import cv2

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return 0.0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower = np.array([100, 80, 80])
    upper = np.array([130, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    total = img.shape[0] * img.shape[1]
    return float(np.sum(mask > 0)) / total


def _check_flash(cam_id):
    """
    Analyse l'historique d'intensité bleue pour cam_id.
    Retourne True si un pattern de clignotement est détecté.
    """
    hist = BLUE_HISTORY.get(cam_id, [])
    if len(hist) < 4:
        return False
    arr = np.array(hist)
    mean_val = float(np.mean(arr))
    variance = float(np.var(arr))
    return mean_val >= BLUE_FLASH_MIN_MEAN and variance >= BLUE_FLASH_MIN_VAR


def start_anpr_worker(app, socketio, latest_frames, send_alert_fn, frame_buffers=None):
    """
    Démarre le worker ANPR en arrière-plan.

    Args:
        app: Flask app (pour app_context)
        socketio: SocketIO instance (pour emit threat_alert)
        latest_frames: dict LATEST_FRAMES {camera_id: bytes}
        send_alert_fn: fonction send_alert(user_id, message, gif_bytes=None)
        frame_buffers: dict FRAME_BUFFERS {camera_id: deque(bytes)} pour GIF alertes
    """
    if os.environ.get("ANPR_ENABLED", "true").lower() == "false":
        logger.info("[ANPR Worker] Disabled via ANPR_ENABLED=false")
        return

    def worker_loop():
        # Import ici pour éviter import circulaire + lazy load du modèle
        from anpr_engine import ANPREngine
        from models import db, Camera, Blacklist, PlateDetection

        logger.info(f"[ANPR Worker] Starting (interval={ANPR_INTERVAL}s)")

        # Charger le moteur (singleton — 1 seule fois)
        try:
            engine = eventlet.tpool.execute(ANPREngine.get_instance)
            logger.info("[ANPR Worker] Engine loaded successfully")
        except Exception as e:
            logger.error(f"[ANPR Worker] Failed to load engine: {e}")
            return

        while True:
            try:
                with app.app_context():
                    cameras = Camera.query.all()

                    for camera in cameras:
                        cam_id = camera.id

                        # Pas de frame disponible ?
                        if cam_id not in latest_frames:
                            continue

                        # Déjà scanné récemment ?
                        last = ANPR_LAST_SCAN.get(cam_id, 0)
                        if time.time() - last < ANPR_INTERVAL:
                            continue

                        ANPR_LAST_SCAN[cam_id] = time.time()
                        frame_bytes = latest_frames[cam_id]

                        # ── Gyrophare : mesure couverture bleue (rapide, CPU) ──
                        flash_enabled = getattr(camera, "flash_detect_enabled", True)
                        if flash_enabled is None:
                            flash_enabled = True
                        try:
                            blue_cov = (
                                eventlet.tpool.execute(
                                    _measure_blue_coverage, frame_bytes
                                )
                                if flash_enabled
                                else 0.0
                            )
                        except Exception:
                            blue_cov = 0.0

                        if cam_id not in BLUE_HISTORY:
                            BLUE_HISTORY[cam_id] = []
                        BLUE_HISTORY[cam_id].append(blue_cov)
                        if len(BLUE_HISTORY[cam_id]) > BLUE_HISTORY_SIZE:
                            BLUE_HISTORY[cam_id].pop(0)

                        if _check_flash(cam_id):
                            last_flash = BLUE_FLASH_LAST.get(cam_id, 0)
                            if time.time() - last_flash >= BLUE_FLASH_DEDUP:
                                BLUE_FLASH_LAST[cam_id] = time.time()
                                logger.info(
                                    f"[ANPR] Gyrophare bleu détecté cam={camera.name} "
                                    f"cov={blue_cov:.3f}"
                                )
                                alert_msg = (
                                    f"🚨 ALERTE PCS 🚨\n"
                                    f"Véhicule d'urgence détecté !\n"
                                    f"Gyrophare bleu en approche\n"
                                    f"Caméra: {camera.name}"
                                )
                                try:
                                    send_alert_fn(camera.user_id, alert_msg)
                                except Exception as e:
                                    logger.warning(f"[ANPR] Flash alert error: {e}")
                                socketio.emit(
                                    "emergency_flash",
                                    {
                                        "camera_id": cam_id,
                                        "camera_name": camera.name,
                                        "type": "blue_flash",
                                        "coverage": round(blue_cov, 3),
                                    },
                                    room=f"user_{camera.user_id}",
                                )

                        # Inference dans un vrai thread OS (eventlet.tpool)
                        from models import (
                            db,
                            Camera,
                            Blacklist,
                            PlateDetection,
                            RoboflowModel,
                        )

                        # ── 1. YOLO + OCR (local) ──
                        try:
                            results = eventlet.tpool.execute(
                                engine.detect_plates, frame_bytes
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ANPR Worker] Inference error cam {cam_id}: {e}"
                            )
                            continue

                        # ── 2. Roboflow multi-modèle ──
                        rf_models = RoboflowModel.query.filter_by(
                            user_id=camera.user_id, is_active=True
                        ).all()

                        rf_detections = []  # list of (rf_det_dict, RoboflowModel)
                        for rfm in rf_models:
                            try:
                                rf_dets = eventlet.tpool.execute(
                                    engine.detect_with_roboflow,
                                    frame_bytes,
                                    rfm.api_key,
                                    rfm.model_endpoint,
                                )
                                for rd in rf_dets:
                                    rf_detections.append((rd, rfm))
                                if rf_dets:
                                    logger.info(
                                        f"[ANPR] Roboflow model={rfm.name}: "
                                        f"{len(rf_dets)} detections"
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"[ANPR] Roboflow error model={rfm.name}: {e}"
                                )

                        # ── 3. Merger Roboflow avec YOLO (IoU > 0.4) ──
                        # Enrichir les détections YOLO avec les classes Roboflow
                        for rd, rfm in rf_detections:
                            rf_bbox = rd.get("bbox", [0, 0, 0, 0])
                            merged = False
                            for det in results:
                                iou = compute_iou(rf_bbox, det.get("bbox", [0, 0, 0, 0]))
                                if iou > 0.4:
                                    # Enrichir la détection YOLO existante
                                    det["rf_class"] = rd.get("rf_class", "")
                                    det["rf_model_name"] = rfm.name
                                    det["rf_confidence"] = rd.get("confidence", 0)
                                    # Garder vehicle_type de YOLO (car, truck…), pas la classe RF
                                    if det["vehicle_color"] == "unknown":
                                        det["vehicle_color"] = rd.get("vehicle_color", det["vehicle_color"])
                                    merged = True
                                    break
                            if not merged:
                                # Détection Roboflow sans correspondance YOLO
                                # vehicle_type reste "unknown" — la vraie info est dans rf_class
                                results.append({
                                    "plate": None,
                                    "confidence": rd.get("confidence", 0),
                                    "bbox": rf_bbox,
                                    "vehicle_type": "unknown",
                                    "vehicle_color": rd.get("vehicle_color", "unknown"),
                                    "rf_class": rd.get("rf_class", ""),
                                    "rf_model_name": rfm.name,
                                    "rf_confidence": rd.get("confidence", 0),
                                })

                        # Chargement des objets suivis (tracking)
                        if cam_id not in CAMERA_TRACKS:
                            CAMERA_TRACKS[cam_id] = []
                        current_tracks = CAMERA_TRACKS[cam_id]

                        for detection in results:
                            plate = detection.get("plate")
                            confidence = detection.get("confidence", 0)
                            veh_type = detection.get("vehicle_type", "unknown")
                            veh_color = detection.get("vehicle_color", "unknown")
                            bbox = detection.get("bbox")
                            rf_class = detection.get("rf_class", "")
                            rf_model_name = detection.get("rf_model_name", "")

                            # 1. Vérification avec les frames précédentes (Tracking IoU)
                            best_iou = 0.0
                            best_track = None
                            for t in current_tracks:
                                iou = compute_iou(bbox, t.get("bbox", [0, 0, 0, 0]))
                                if iou > best_iou:
                                    best_iou = iou
                                    best_track = t

                            is_new_vehicle = False
                            if best_iou > 0.3:
                                # C'est un véhicule déjà vu (garé ou qui bouge lentement)
                                best_track["bbox"] = bbox
                                best_track["last_seen"] = time.time()

                                # Mise à jour des informations si elles sont meilleures
                                if plate and not best_track["plate"]:
                                    best_track["plate"] = plate
                                    is_new_vehicle = (
                                        True  # On veut loguer la nouvelle info (plaque)
                                    )
                                if (
                                    veh_color != "unknown"
                                    and best_track["color"] == "unknown"
                                ):
                                    best_track["color"] = veh_color
                                if (
                                    veh_type != "unknown"
                                    and best_track["type"] == "unknown"
                                ):
                                    best_track["type"] = veh_type
                            else:
                                # C'est un nouveau véhicule qui entre dans le cadre
                                t_id = TRACK_ID_COUNTER.get(cam_id, 1)
                                TRACK_ID_COUNTER[cam_id] = t_id + 1
                                new_track = {
                                    "id": t_id,
                                    "bbox": bbox,
                                    "plate": plate,
                                    "type": veh_type,
                                    "color": veh_color,
                                    "last_seen": time.time(),
                                }
                                current_tracks.append(new_track)
                                is_new_vehicle = True

                            # Clé déduplique pour ne pas SPAMMER les alertes
                            dedup_key = (
                                (cam_id, plate)
                                if plate
                                else (cam_id, veh_type, veh_color, rf_class)
                            )
                            last_seen = RECENT_DETECTIONS.get(dedup_key, 0)

                            # On ne continue le process (alerte, etc) que si c'est nouveau ou hors cooldown
                            if not is_new_vehicle and (
                                time.time() - last_seen < DEDUP_WINDOW
                            ):
                                continue

                            RECENT_DETECTIONS[dedup_key] = time.time()

                            # --- Chercher une correspondance blacklist ---
                            matched_rule = None

                            # 1. Correspondance par plaque (règles standard)
                            if plate:
                                rule = Blacklist.query.filter_by(
                                    user_id=camera.user_id,
                                    plate_normalized=plate,
                                    match_plate=True,
                                ).first()
                                if rule:
                                    # Vérifier aussi vehicle_type et vehicle_color si spécifiés
                                    type_ok = (
                                        not rule.vehicle_type
                                        or rule.vehicle_type == "any"
                                        or rule.vehicle_type == veh_type
                                    )
                                    color_ok = (
                                        not rule.vehicle_color
                                        or rule.vehicle_color == "any"
                                        or rule.vehicle_color == veh_color
                                    )
                                    if type_ok and color_ok:
                                        matched_rule = rule

                            # 2. Correspondance par type+couleur sans plaque (mode urgence)
                            if not matched_rule:
                                type_color_rules = Blacklist.query.filter_by(
                                    user_id=camera.user_id,
                                    match_plate=False,
                                ).all()
                                for rule in type_color_rules:
                                    type_is_specific = (
                                        rule.vehicle_type
                                        and rule.vehicle_type not in ("any", "")
                                    )
                                    color_is_specific = (
                                        rule.vehicle_color
                                        and rule.vehicle_color not in ("any", "")
                                    )
                                    if not type_is_specific and not color_is_specific:
                                        logger.warning(
                                            f"[ANPR] Règle blacklist #{rule.id} ignorée "
                                            f"(match_plate=False sans critère spécifique — wildcard)"
                                        )
                                        continue

                                    type_ok = (
                                        not type_is_specific
                                        or rule.vehicle_type == veh_type
                                    )
                                    color_ok = (
                                        not color_is_specific
                                        or rule.vehicle_color == veh_color
                                    )
                                    if type_ok and color_ok:
                                        matched_rule = rule
                                        break

                            is_threat = matched_rule is not None

                            # ── Roboflow per-model alert logic ──
                            rf_alert = False
                            rf_alert_priority = "normal"
                            rf_alert_label = ""
                            if rf_class and rf_model_name:
                                # Trouver le modèle correspondant
                                rfm_match = None
                                for rfm in rf_models:
                                    if rfm.name == rf_model_name:
                                        rfm_match = rfm
                                        break
                                if rfm_match:
                                    if rfm_match.alert_mode == "alert_all":
                                        rf_alert = True
                                        rf_alert_priority = rfm_match.alert_priority or "normal"
                                        rf_alert_label = f"IA: {rf_class} ({rfm_match.name})"
                                    elif rfm_match.alert_mode == "alert_on_classes":
                                        alert_classes = rfm_match.get_alert_classes()
                                        # Match case-insensitive
                                        if any(
                                            rf_class.lower() == c.lower()
                                            for c in alert_classes
                                        ):
                                            rf_alert = True
                                            rf_alert_priority = rfm_match.alert_priority or "normal"
                                            rf_alert_label = f"IA: {rf_class} ({rfm_match.name})"
                                    # log_only → rf_alert reste False

                            # Log en DB : tout véhicule identifiable (pas le fallback "unknown")
                            # + toujours logger les menaces même si YOLO n'a rien vu
                            # + toujours logger les détections Roboflow
                            should_log = (
                                plate is not None          # plaque lue
                                or veh_type != "unknown"   # véhicule identifié (car, bus…)
                                or is_threat               # menace même sans plaque
                                or rf_class                # détection Roboflow
                            )
                            if should_log:
                                det = PlateDetection(
                                    user_id=camera.user_id,
                                    camera_id=cam_id,
                                    plate_normalized=plate or "",
                                    vehicle_type=veh_type,
                                    vehicle_color=veh_color,
                                    confidence=confidence,
                                    is_threat=is_threat or rf_alert,
                                    roboflow_model_name=rf_model_name,
                                    detected_class=rf_class,
                                )
                                db.session.add(det)
                                db.session.commit()

                                # Émettre en temps réel pour le dashboard
                                socketio.emit(
                                    "new_detection",
                                    {
                                        "id": det.id,
                                        "plate": plate or "",
                                        "vehicle_type": veh_type,
                                        "vehicle_color": veh_color,
                                        "confidence": round(confidence, 3),
                                        "is_threat": is_threat or rf_alert,
                                        "camera_name": camera.name,
                                        "camera_id": cam_id,
                                        "detected_at": det.detected_at.strftime("%H:%M:%S"),
                                        "roboflow_model_name": rf_model_name,
                                        "detected_class": rf_class,
                                    },
                                    room=f"user_{camera.user_id}",
                                )

                            logger.info(
                                f"[ANPR] cam={camera.name} plate={plate} "
                                f"type={veh_type} color={veh_color} "
                                f"conf={confidence:.2f} threat={is_threat}"
                                f"{' rf=' + rf_class + '(' + rf_model_name + ')' if rf_class else ''}"
                            )

                            # Alerte si blacklisté OU alerte Roboflow
                            if is_threat or rf_alert:
                                if matched_rule:
                                    label = (
                                        matched_rule.alert_label or "Véhicule Suspect"
                                    ).strip()
                                    priority = matched_rule.alert_priority or "normal"
                                    reason = matched_rule.description
                                elif rf_alert:
                                    label = rf_alert_label
                                    priority = rf_alert_priority
                                    reason = f"Détecté par modèle IA: {rf_model_name}"
                                else:
                                    label = "Véhicule Suspect"
                                    priority = "normal"
                                    reason = "Correspondance blacklist"

                                priority_icon = {
                                    "critical": "🔴",
                                    "high": "🟠",
                                    "normal": "🟡",
                                }.get(priority, "🟡")

                                if plate:
                                    plate_line = f"Plaque: {plate}"
                                elif rf_class:
                                    plate_line = f"Classe IA: {rf_class} | Couleur: {veh_color}"
                                else:
                                    plate_line = f"Type: {veh_type} | Couleur: {veh_color}"
                                alert_msg = (
                                    f"{priority_icon} ALERTE PCS {priority_icon}\n"
                                    f"{label}!\n"
                                    f"{plate_line}\n"
                                    f"Caméra: {camera.name}\n"
                                    f"Raison: {reason}"
                                )
                                # Générer un GIF des dernières frames si le buffer est disponible
                                gif_bytes = None
                                gif_rel_path = ""
                                if frame_buffers:
                                    try:
                                        from alert_clip import create_alert_gif
                                        gif_bytes = eventlet.tpool.execute(
                                            create_alert_gif, frame_buffers, cam_id, 20
                                        )
                                        # Sauvegarder le GIF sur disque
                                        if gif_bytes:
                                            gif_dir = os.path.join(
                                                os.path.dirname(__file__), "static", "alert_gifs"
                                            )
                                            os.makedirs(gif_dir, exist_ok=True)
                                            gif_filename = f"alert_{det.id}_{cam_id}_{int(time.time())}.gif"
                                            gif_full = os.path.join(gif_dir, gif_filename)
                                            with open(gif_full, "wb") as gf:
                                                gf.write(gif_bytes)
                                            gif_rel_path = f"alert_gifs/{gif_filename}"
                                            det.gif_path = gif_rel_path
                                            db.session.commit()
                                            logger.info(f"[ANPR] GIF saved: {gif_rel_path}")
                                    except Exception as e:
                                        logger.warning(f"[ANPR] GIF creation error: {e}")

                                try:
                                    send_alert_fn(camera.user_id, alert_msg, gif_bytes=gif_bytes)
                                except Exception as e:
                                    logger.warning(f"[ANPR] Alert send error: {e}")

                                socketio.emit(
                                    "threat_alert",
                                    {
                                        "camera_id": cam_id,
                                        "camera_name": camera.name,
                                        "plate": plate or f"{veh_type}/{veh_color}",
                                        "vehicle_type": veh_type,
                                        "vehicle_color": veh_color,
                                        "label": label,
                                        "priority": priority,
                                        "reason": reason,
                                        "rf_class": rf_class,
                                        "rf_model": rf_model_name,
                                    },
                                    room=f"user_{camera.user_id}",
                                )

                # Nettoyage cache déduplique (retirer les entrées expirées)
                now = time.time()
                expired = [
                    k
                    for k, v in RECENT_DETECTIONS.items()
                    if now - v > DEDUP_WINDOW * 2
                ]
                for k in expired:
                    del RECENT_DETECTIONS[k]

                # Nettoyage cache tracking
                for cam_id in list(CAMERA_TRACKS.keys()):
                    CAMERA_TRACKS[cam_id] = [
                        t
                        for t in CAMERA_TRACKS[cam_id]
                        if now - t["last_seen"] < TRACK_EXPIRY_SECONDS
                    ]

            except Exception as e:
                logger.error(f"[ANPR Worker] Cycle error: {e}")

            # Pause avant le prochain cycle complet
            eventlet.sleep(1)

    eventlet.spawn(worker_loop)
    logger.info("[ANPR Worker] Spawned background greenlet")
