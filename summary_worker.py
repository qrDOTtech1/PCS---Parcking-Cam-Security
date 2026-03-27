"""
PCS — Summary Worker
Génère périodiquement des résumés structurés par caméra,
stockés dans CameraSummary et consommables par les futures IA PCS-AI.

Chaque résumé couvre la fenêtre [period_start, period_end] et contient :
  - plates_detected      : plaques vues (liste)
  - threats              : règles blacklist déclenchées
  - vehicle_counts       : comptage par type (car/truck/bus/motorcycle)
  - color_counts         : comptage par couleur dominante
  - emergency_flashes    : nb de gyrophares bleus détectés
  - frames_received      : nb de frames traitées
  - camera_online        : true/false
  - gps                  : {lat, lng, speed} si disponible

L'IA externe peut :
  - GET  /api/ai/summaries?since=<iso>&camera_id=<id>   → poll nouveaux résumés
  - GET  /api/ai/latest                                  → dernier résumé par cam
  - POST /api/ai/summaries/<id>/response                 → écrire analyse IA
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta

import eventlet
import eventlet.tpool

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = int(os.environ.get("SUMMARY_INTERVAL_SECONDS", "60"))


def start_summary_worker(app, latest_frames, camera_status, blue_flash_last):
    """
    Démarre le worker de résumés en arrière-plan (greenlet eventlet).

    Args:
        app              : Flask app (pour app_context)
        latest_frames    : dict LATEST_FRAMES {camera_id: bytes}
        camera_status    : dict CAMERA_STATUS {camera_id: {connected, frame_count, …}}
        blue_flash_last  : dict BLUE_FLASH_LAST {camera_id: timestamp}  (depuis anpr_worker)
    """
    if os.environ.get("SUMMARY_ENABLED", "true").lower() == "false":
        logger.info("[Summary Worker] Disabled via SUMMARY_ENABLED=false")
        return

    def worker_loop():
        from models import db, Camera, User, PlateDetection, CameraSummary, AIConfig, SystemConfig

        logger.info(f"[Summary Worker] Starting (default interval={DEFAULT_INTERVAL}s)")

        # Mémorise combien de flashes on a déjà comptés pour éviter double-comptage
        flash_seen_at = {}   # camera_id -> timestamp du dernier flash connu

        # Mémorise le nb de détections déjà incluses dans un résumé précédent
        last_det_id = {}     # camera_id -> max id de PlateDetection dans dernier résumé

        # Timestamp de début de la fenêtre courante, par caméra
        window_start = {}    # camera_id -> datetime

        while True:
            try:
                with app.app_context():
                    cameras = Camera.query.all()
                    now = datetime.utcnow()

                    # Récupère les intervalles personnalisés par utilisateur
                    user_intervals = {}
                    for cfg in SystemConfig.query.filter_by(key_name="summary_interval").all():
                        try:
                            user_intervals[cfg.user_id] = int(cfg.key_value)
                        except (TypeError, ValueError):
                            pass
                    # AIConfig override
                    for ai in AIConfig.query.all():
                        if ai.summary_interval:
                            user_intervals[ai.user_id] = ai.summary_interval

                    for camera in cameras:
                        cam_id = camera.id
                        interval = user_intervals.get(camera.user_id, DEFAULT_INTERVAL)

                        # Initialiser la fenêtre si première fois
                        if cam_id not in window_start:
                            window_start[cam_id] = now
                            last_det_id[cam_id] = 0
                            flash_seen_at[cam_id] = blue_flash_last.get(cam_id, 0)
                            continue

                        # Vérifier si la fenêtre est écoulée
                        elapsed = (now - window_start[cam_id]).total_seconds()
                        if elapsed < interval:
                            continue

                        period_start = window_start[cam_id]
                        period_end   = now
                        window_start[cam_id] = now  # reset fenêtre

                        # ── Collecter les données de la fenêtre ──────────────────

                        # Détections de plaques
                        detections = PlateDetection.query.filter(
                            PlateDetection.camera_id == cam_id,
                            PlateDetection.detected_at >= period_start,
                            PlateDetection.detected_at < period_end,
                        ).all()

                        plates_seen   = list({d.plate_normalized for d in detections if d.plate_normalized})
                        threats       = [
                            {"plate": d.plate_normalized, "at": d.detected_at.isoformat()}
                            for d in detections if d.is_threat
                        ]

                        # Comptage gyrophare : nb de flash survenus dans la fenêtre
                        flash_ts = blue_flash_last.get(cam_id, 0)
                        prev_ts  = flash_seen_at.get(cam_id, 0)
                        new_flash = 1 if flash_ts > prev_ts and flash_ts >= period_start.timestamp() else 0
                        flash_seen_at[cam_id] = flash_ts

                        # Statut caméra
                        status    = camera_status.get(cam_id, {})
                        online    = status.get("connected", False)
                        frames_rx = status.get("frame_count", 0)

                        # GPS
                        gps = None
                        if camera.lat and camera.lng:
                            gps = {"lat": camera.lat, "lng": camera.lng,
                                   "speed": camera.speed}

                        # Résumé structuré
                        summary = {
                            "schema_version": "1.0",
                            "period_start": period_start.isoformat(),
                            "period_end":   period_end.isoformat(),
                            "duration_seconds": round(elapsed),
                            "camera_id":    cam_id,
                            "camera_name":  camera.name,
                            "camera_online": online,
                            "frames_received": frames_rx,
                            "gps": gps,
                            "plates_detected": plates_seen,
                            "plates_count": len(plates_seen),
                            "threats": threats,
                            "threats_count": len(threats),
                            "emergency_flashes": new_flash,
                            # Champs réservés pour l'IA — remplis par l'IA externe
                            "ai_notes": None,
                            "anomalies": [],
                        }

                        rec = CameraSummary(
                            user_id=camera.user_id,
                            camera_id=cam_id,
                            period_start=period_start,
                            period_end=period_end,
                            summary_json=json.dumps(summary),
                            ai_processed=False,
                        )
                        db.session.add(rec)

                        # Purge des résumés > 7 jours pour ne pas surcharger la DB
                        cutoff = now - timedelta(days=7)
                        old = CameraSummary.query.filter(
                            CameraSummary.camera_id == cam_id,
                            CameraSummary.created_at < cutoff,
                        ).all()
                        for o in old:
                            db.session.delete(o)

                        db.session.commit()

                        logger.info(
                            f"[Summary] cam={camera.name} plates={len(plates_seen)} "
                            f"threats={len(threats)} flashes={new_flash} "
                            f"interval={interval}s"
                        )

            except Exception as e:
                logger.error(f"[Summary Worker] Cycle error: {e}")

            eventlet.sleep(5)  # vérifie toutes les 5s si une fenêtre est écoulée

    eventlet.spawn(worker_loop)
    logger.info("[Summary Worker] Spawned background greenlet")
