"""
PCS — ANPR Background Worker
Scanne séquentiellement toutes les caméras actives,
détecte les plaques via ANPREngine, et déclenche les alertes blacklist.

Tourne comme greenlet eventlet dans le même process que Flask.
"""

import os
import time
import logging
import eventlet

logger = logging.getLogger(__name__)

# Intervalle minimum entre 2 scans d'une même caméra (secondes)
ANPR_INTERVAL = int(os.environ.get("ANPR_INTERVAL_SECONDS", "5"))

# Cache pour dédupliquer : (camera_id, plate) -> timestamp
RECENT_DETECTIONS = {}
DEDUP_WINDOW = 60  # secondes — même plaque/même cam ignorée pendant 60s

# Timestamp du dernier scan par caméra
ANPR_LAST_SCAN = {}


def start_anpr_worker(app, socketio, latest_frames, send_alert_fn):
    """
    Démarre le worker ANPR en arrière-plan.

    Args:
        app: Flask app (pour app_context)
        socketio: SocketIO instance (pour emit threat_alert)
        latest_frames: dict LATEST_FRAMES {camera_id: bytes}
        send_alert_fn: fonction send_alert(user_id, message)
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

                        # Inference dans un vrai thread OS (eventlet.tpool)
                        try:
                            results = eventlet.tpool.execute(
                                engine.detect_plates, frame_bytes
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ANPR Worker] Inference error cam {cam_id}: {e}"
                            )
                            continue

                        for detection in results:
                            plate = detection.get("plate")
                            if not plate:
                                continue
                            confidence = detection.get("confidence", 0)

                            # Déduplique
                            dedup_key = (cam_id, plate)
                            last_seen = RECENT_DETECTIONS.get(dedup_key, 0)
                            if time.time() - last_seen < DEDUP_WINDOW:
                                continue
                            RECENT_DETECTIONS[dedup_key] = time.time()

                            # Log en DB
                            blacklisted = Blacklist.query.filter_by(
                                user_id=camera.user_id,
                                plate_normalized=plate,
                            ).first()
                            is_threat = blacklisted is not None

                            det = PlateDetection(
                                user_id=camera.user_id,
                                camera_id=cam_id,
                                plate_normalized=plate,
                                confidence=confidence,
                                is_threat=is_threat,
                            )
                            db.session.add(det)
                            db.session.commit()

                            logger.info(
                                f"[ANPR] Plate={plate} cam={camera.name} "
                                f"conf={confidence:.2f} threat={is_threat}"
                            )

                            # Alerte si blacklisté
                            if is_threat:
                                alert_msg = (
                                    f"🚨 ALERTE PCS 🚨\n"
                                    f"Véhicule Suspect!\n"
                                    f"Plaque: {plate}\n"
                                    f"Caméra: {camera.name}\n"
                                    f"Raison: {blacklisted.description}"
                                )
                                try:
                                    send_alert_fn(camera.user_id, alert_msg)
                                except Exception as e:
                                    logger.warning(f"[ANPR] Alert send error: {e}")

                                socketio.emit(
                                    "threat_alert",
                                    {
                                        "camera_id": cam_id,
                                        "camera_name": camera.name,
                                        "plate": plate,
                                        "reason": blacklisted.description,
                                    },
                                    room=f"user_{camera.user_id}",
                                )

                # Nettoyage cache déduplique (retirer les entrées expirées)
                now = time.time()
                expired = [
                    k for k, v in RECENT_DETECTIONS.items()
                    if now - v > DEDUP_WINDOW * 2
                ]
                for k in expired:
                    del RECENT_DETECTIONS[k]

            except Exception as e:
                logger.error(f"[ANPR Worker] Cycle error: {e}")

            # Pause avant le prochain cycle complet
            eventlet.sleep(1)

    eventlet.spawn(worker_loop)
    logger.info("[ANPR Worker] Spawned background greenlet")
