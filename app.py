import eventlet

eventlet.monkey_patch()

import os
import io
import re
import json
import random
import urllib.parse
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from functools import wraps

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    session,
    Response,
    flash,
    send_file,
)
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from models import (
    db,
    User,
    Camera,
    Blacklist,
    NotificationTarget,
    SystemConfig,
    PlateDetection,
    CameraSummary,
    AIConfig,
)
from sqlalchemy import text
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constantes globales
LATEST_FRAMES = {}
CAMERA_SOCKETS = {}
FRAME_BUFFERS = {}
FRAME_TIMESTAMPS = {}
BUFFER_SIZE = 60
BUFFER_SECONDS = 3
FRAME_SEQUENCES = {}
CAMERA_STATUS = {}
MJPEG_STREAMS = {}
PUBLIC_CAMERA_FRAMES = {}  # camera_id -> {frame, last_seen, name}

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.environ.get("SECRET_KEY", "parkingcam-secret-key-change-in-prod"),
)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///parkingcam.db"
)
app.config["MASTER_KEY"] = os.environ.get("MASTER_KEY", "master_key_pcs_2024")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    ping_timeout=30,
    ping_interval=10,
)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)


# Décorateur pour vérifier l'authentification
def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


# Décorateur pour vérifier l'authentification admin
def require_admin_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        key = request.headers.get("X-Master-Key")
        if not key or key != os.environ.get("MASTER_KEY", "master_key_pcs_2024"):
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated_function


def normalize_plate(plate_str):
    if not plate_str:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(plate_str).upper())


def send_alert(user_id, message):
    targets = NotificationTarget.query.filter_by(user_id=user_id, is_active=True).all()
    encoded_message = urllib.parse.quote_plus(message)
    for target in targets:
        if target.platform == "signal" and target.api_key:
            if target.api_key.startswith("http"):
                base_url = target.api_key
                if "&text=" in base_url:
                    base_url = base_url.split("&text=")[0]
                url = f"{base_url}&text={encoded_message}"
                phone_display = "URL Signal"
            elif target.phone_number:
                url = f"https://api.callmebot.com/signal/send.php?phone={target.phone_number}&apikey={target.api_key}&text={encoded_message}"
                phone_display = target.phone_number
            else:
                continue

            try:
                requests.get(url, timeout=3)
            except Exception as e:
                logger.error(f"Erreur Signal {phone_display}: {e}")

        elif target.platform == "telegram" and target.bot_token and target.chat_id:
            url = f"https://api.telegram.org/bot{target.bot_token}/sendMessage"
            data = {"chat_id": target.chat_id, "text": message}
            try:
                requests.post(url, data=data, timeout=3)
            except Exception as e:
                logger.error(f"Erreur Telegram {target.chat_id}: {e}")


@app.before_request
def create_tables():
    app.before_request_funcs[None].remove(create_tables)
    db.create_all()

    with db.engine.connect() as conn:
        # Migration: Fix blacklist.plate_normalized constraint
        try:
            # Check if column is NOT NULL
            cursor = conn.execute(text("PRAGMA table_info(blacklist)"))
            columns = cursor.fetchall()
            for col in columns:
                # col[1] is name, col[3] is notnull
                if col[1] == "plate_normalized" and col[3] == 1:
                    logger.info(
                        "Migrating blacklist table to allow NULL plate_normalized"
                    )
                    conn.execute(
                        text(
                            "CREATE TABLE blacklist_new ("
                            "id INTEGER PRIMARY KEY, "
                            "user_id INTEGER NOT NULL, "
                            "plate_normalized VARCHAR(20), "
                            "description VARCHAR(200), "
                            "is_police BOOLEAN, "
                            "vehicle_type VARCHAR(20), "
                            "vehicle_color VARCHAR(20), "
                            "alert_label VARCHAR(50), "
                            "alert_priority VARCHAR(10), "
                            "match_plate BOOLEAN, "
                            "FOREIGN KEY(user_id) REFERENCES user(id))"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO blacklist_new SELECT id, user_id, plate_normalized, description, is_police, vehicle_type, vehicle_color, alert_label, alert_priority, match_plate FROM blacklist"
                        )
                    )
                    conn.execute(text("DROP TABLE blacklist"))
                    conn.execute(text("ALTER TABLE blacklist_new RENAME TO blacklist"))
                    conn.commit()
                    break
        except Exception as e:
            logger.error(f"Migration error: {e}")

        try:
            conn.execute(
                text("ALTER TABLE camera ADD COLUMN address VARCHAR(200) DEFAULT ''")
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE plate_detection ADD COLUMN vehicle_type VARCHAR(20) DEFAULT 'unknown'"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE plate_detection ADD COLUMN vehicle_color VARCHAR(20) DEFAULT 'unknown'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE user ADD COLUMN subscription_mode VARCHAR(30) DEFAULT 'standard'"
                )
            )
        except:
            pass

        try:
            conn.execute(
                text(
                    "ALTER TABLE camera ADD COLUMN recording_enabled INTEGER DEFAULT 1"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE camera ADD COLUMN stream_auto_mode VARCHAR(20) DEFAULT 'off'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE camera ADD COLUMN is_streaming INTEGER DEFAULT 0")
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE camera ADD COLUMN flash_detect_enabled INTEGER DEFAULT 1"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE camera ADD COLUMN address VARCHAR(200) DEFAULT ''")
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE user ADD COLUMN subscription_mode VARCHAR(30) DEFAULT 'standard'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE user ADD COLUMN max_cameras INTEGER DEFAULT 3")
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE blacklist ADD COLUMN vehicle_type VARCHAR(20) DEFAULT 'any'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE blacklist ADD COLUMN vehicle_color VARCHAR(20) DEFAULT 'any'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE blacklist ADD COLUMN alert_label VARCHAR(50) DEFAULT ''"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text(
                    "ALTER TABLE blacklist ADD COLUMN alert_priority VARCHAR(10) DEFAULT 'normal'"
                )
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE blacklist ADD COLUMN match_plate INTEGER DEFAULT 1")
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE user ADD COLUMN max_blacklist INTEGER DEFAULT 50")
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE user ADD COLUMN features_json TEXT DEFAULT '[]'")
            )
        except:
            pass
        try:
            conn.execute(
                text("ALTER TABLE user ADD COLUMN admin_notes TEXT DEFAULT ''")
            )
        except:
            pass
        conn.commit()

    # Démarrer le worker ANPR (une seule fois, au premier request)
    from anpr_worker import start_anpr_worker, BLUE_FLASH_LAST

    start_anpr_worker(app, socketio, LATEST_FRAMES, send_alert)

    # Démarrer le worker de résumés (pour PCS-AI)
    from summary_worker import start_summary_worker

    start_summary_worker(app, LATEST_FRAMES, CAMERA_STATUS, BLUE_FLASH_LAST)


def get_max_notifications(user):
    # Emergency: 20, Standard: 3
    if user.subscription_mode == "emergency":
        return 20
    return 3


@app.route("/upload", methods=["POST"])
@limiter.limit("20 per minute")
def upload_image():
    api_key = request.headers.get("X-API-Key") or request.form.get("api_key")
    if not api_key:
        return jsonify({"status": "error", "message": "Missing API Key"}), 401

    camera = Camera.query.filter_by(api_key=api_key).first()
    if not camera:
        return jsonify({"status": "error", "message": "Invalid API Key"}), 401

    user_id = camera.user_id
    camera.last_seen = datetime.utcnow()
    db.session.commit()

    if "image" not in request.files:
        return jsonify({"status": "error", "message": "No image part"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "No selected file"}), 400

    if file:
        import eventlet
        import eventlet.tpool
        from anpr_engine import ANPREngine

        image_bytes = file.read()
        force_plate = request.form.get("force_plate")

        try:
            if force_plate:
                # Mode test : forcer une plaque
                detections = [
                    {"plate": normalize_plate(force_plate), "confidence": 1.0}
                ]
            else:
                engine = ANPREngine.get_instance()
                detections = eventlet.tpool.execute(engine.detect_plates, image_bytes)

            if detections:
                best = detections[0]
                normalized_read = best["plate"]

                blacklisted = Blacklist.query.filter_by(
                    user_id=user_id, plate_normalized=normalized_read
                ).first()
                threat = False

                if blacklisted:
                    threat = True
                    alert_msg = f"🚨 ALERTE PCS 🚨\nVéhicule Suspect!\nPlaque: {normalized_read}\nCaméra: {camera.name}\nRaison: {blacklisted.description}"
                    send_alert(user_id, alert_msg)
                    socketio.emit(
                        "threat_alert",
                        {
                            "camera_id": camera.id,
                            "camera_name": camera.name,
                            "plate": normalized_read,
                            "reason": blacklisted.description,
                        },
                        room=f"user_{user_id}",
                    )

                return jsonify(
                    {"status": "success", "plate": normalized_read, "threat": threat}
                ), 200
            else:
                return jsonify(
                    {"status": "success", "plate": None, "threat": False}
                ), 200

        except Exception as e:
            logger.error(f"[ANPR] Detection error: {e}")
            return jsonify({"status": "error", "message": "ANPR engine error"}), 500

    return jsonify({"status": "error", "message": "Unknown error"}), 500


@app.route("/ping", methods=["GET"])
def ping():
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if not api_key:
        return jsonify({"status": "error", "message": "Missing API Key"}), 401
    camera = Camera.query.filter_by(api_key=api_key).first()
    if not camera:
        return jsonify({"status": "error", "message": "Invalid API Key"}), 401

    camera.last_seen = datetime.utcnow()
    db.session.commit()
    return jsonify(
        {
            "status": "success",
            "message": "Pong",
            "config": camera.get_config(),
            "stream_auto_mode": camera.stream_auto_mode,
            "recording_enabled": camera.recording_enabled,
        }
    ), 200


@app.route("/public_stream/<camera_id>", methods=["POST"])
def public_stream(camera_id):
    if not re.match(r"^[a-zA-Z0-9_-]{1,32}$", camera_id):
        return "Invalid camera ID", 400
    if "image" in request.files:
        frame_data = request.files["image"].read()
        PUBLIC_CAMERA_FRAMES[camera_id] = {
            "frame": frame_data,
            "last_seen": datetime.utcnow(),
            "name": request.form.get("name", camera_id),
        }
        return "OK", 200
    return "No image", 400


@app.route("/stream_upload", methods=["POST"])
def stream_upload():
    api_key = request.headers.get("X-API-Key") or request.form.get("api_key")
    if not api_key:
        return "Missing API Key", 401

    camera = Camera.query.filter_by(api_key=api_key).first()
    if not camera:
        return "Invalid API Key", 401

    camera.last_seen = datetime.utcnow()

    lat = request.form.get("lat")
    lng = request.form.get("lng")
    speed = request.form.get("speed")
    if lat and lng:
        try:
            camera.lat = float(lat)
            camera.lng = float(lng)
            if speed:
                camera.speed = float(speed)
        except ValueError:
            pass

    db.session.commit()

    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename != "":
            frame_data = file.read()
            now = datetime.utcnow()
            LATEST_FRAMES[camera.id] = frame_data

            CAMERA_STATUS[camera.id] = {
                "connected": True,
                "last_frame": now,
                "frame_count": CAMERA_STATUS.get(camera.id, {}).get("frame_count", 0)
                + 1,
            }

            if camera.id not in FRAME_BUFFERS:
                FRAME_BUFFERS[camera.id] = []
                FRAME_TIMESTAMPS[camera.id] = []
                FRAME_SEQUENCES[camera.id] = 0

            FRAME_BUFFERS[camera.id].append(frame_data)
            FRAME_TIMESTAMPS[camera.id].append(now)
            FRAME_SEQUENCES[camera.id] += 1

            while len(FRAME_BUFFERS[camera.id]) > BUFFER_SIZE:
                FRAME_BUFFERS[camera.id].pop(0)
                FRAME_TIMESTAMPS[camera.id].pop(0)

            socketio.emit(
                "frame_update",
                {"camera_id": camera.id, "timestamp": now.isoformat()},
                room=f"camera_{camera.id}",
            )
            return "OK", 200

    return "No image", 400


@app.route("/video_feed/<int:camera_id>")
def video_feed(camera_id):
    if "user_id" not in session:
        return "Unauthorized", 401

    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        return "Unauthorized", 401

    frame = LATEST_FRAMES.get(camera_id)
    if frame:
        return Response(frame, mimetype="image/jpeg")

    return "No frame", 404


def generate_mjpeg_stream(camera_id):
    """Générateur de flux MJPEG avec gestion de buffer"""
    import time

    # Initialiser le flux si nécessaire
    if camera_id not in MJPEG_STREAMS:
        MJPEG_STREAMS[camera_id] = {"clients": 0, "last_frame": None}

    MJPEG_STREAMS[camera_id]["clients"] += 1
    last_sent = b""
    frame_sequence = 0

    try:
        while True:
            # Obtenir la dernière frame disponible
            frame = LATEST_FRAMES.get(camera_id)

            # Envoyer la frame seulement si elle est différente de la précédente
            if frame and frame != last_sent:
                try:
                    # Entête MJPEG standard
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"X-Frame-Seq: " + str(frame_sequence).encode() + b"\r\n"
                        b"\r\n" + frame + b"\r\n"
                    )
                    last_sent = frame
                    frame_sequence += 1

                    # Mettre à jour le statut du flux
                    MJPEG_STREAMS[camera_id]["last_frame"] = datetime.utcnow()
                except Exception as e:
                    logger.error(f"MJPEG stream error for camera {camera_id}: {e}")
                    # En cas d'erreur, attendre un peu avant de réessayer
                    time.sleep(0.1)
                    continue

            # Attendre un court instant avant de vérifier la prochaine frame
            time.sleep(0.033)  # ~30 FPS maximum
    except GeneratorExit:
        # Le client a fermé la connexion
        logger.info(f"MJPEG stream closed for camera {camera_id}")
    finally:
        # Nettoyer les ressources
        if camera_id in MJPEG_STREAMS:
            MJPEG_STREAMS[camera_id]["clients"] = max(
                0, MJPEG_STREAMS[camera_id]["clients"] - 1
            )


@app.route("/mjpeg_stream/<int:camera_id>")
def mjpeg_stream(camera_id):
    if "user_id" not in session:
        return "Unauthorized", 401

    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        return "Unauthorized", 401

    response = Response(
        generate_mjpeg_stream(camera_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Connection"] = "close"
    return response


@app.route("/api/camera/status/<int:camera_id>")
def camera_status(camera_id):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"error": "Not found"}), 404

    status = CAMERA_STATUS.get(camera_id, {"connected": False})
    buffer_size = len(FRAME_BUFFERS.get(camera_id, []))

    # Calculer le nombre de clients connectés au flux MJPEG
    mjpeg_clients = (
        MJPEG_STREAMS.get(camera_id, {}).get("clients", 0)
        if camera_id in MJPEG_STREAMS
        else 0
    )

    return jsonify(
        {
            "connected": status.get("connected", False),
            "last_frame": status.get("last_frame", None),
            "frame_count": status.get("frame_count", 0),
            "buffer_frames": buffer_size,
            "buffer_seconds": buffer_size / 30,
            "mjpeg_clients": mjpeg_clients,
            "is_streaming": camera.is_streaming,
        }
    )


@app.route("/camera/<int:camera_id>/stream.m3u8")
def camera_stream_m3u8(camera_id):
    if "user_id" not in session:
        return "Unauthorized", 401
    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        return "Unauthorized", 401

    m3u8_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=500000
/camera/{camera_id}/stream.ts
"""
    return Response(m3u8_content, mimetype="application/vnd.apple.mpegurl")


@app.route("/camera/<int:camera_id>/stream.ts")
def camera_stream_ts(camera_id):
    if "user_id" not in session:
        return "Unauthorized", 401
    frame = LATEST_FRAMES.get(camera_id)
    if frame:
        return Response(frame, mimetype="video/mp2t")
    return "No frame", 404


@app.route("/api/camera/config/<api_key>", methods=["GET", "POST"])
@require_auth
def camera_config(api_key):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    if request.method == "GET":
        return jsonify(
            {
                "status": "success",
                "config": camera.get_config(),
                "stream_auto_mode": camera.stream_auto_mode,
                "recording_enabled": camera.recording_enabled,
            }
        ), 200

    elif request.method == "POST":
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        if "config" in data:
            camera.set_config(data["config"])
        if "stream_auto_mode" in data:
            camera.stream_auto_mode = data["stream_auto_mode"]
        if "recording_enabled" in data:
            camera.recording_enabled = data["recording_enabled"]

        db.session.commit()

        socketio.emit(
            "config_update",
            {
                "camera_id": camera.id,
                "config": camera.get_config(),
                "stream_auto_mode": camera.stream_auto_mode,
                "recording_enabled": camera.recording_enabled,
            },
            room=f"camera_{camera.id}",
        )

        return jsonify({"status": "success", "message": "Config updated"}), 200


@app.route("/api/camera/recording/<api_key>", methods=["POST"])
@require_auth
def camera_recording(api_key):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    data = request.json or {}
    action = data.get("action")

    if action == "start":
        camera.is_streaming = True
        socketio.emit(
            "recording_started", {"camera_id": camera.id}, room=f"camera_{camera.id}"
        )
    elif action == "stop":
        camera.is_streaming = False
        socketio.emit(
            "recording_stopped", {"camera_id": camera.id}, room=f"camera_{camera.id}"
        )

    db.session.commit()
    return jsonify({"status": "success", "is_streaming": camera.is_streaming}), 200


@app.route("/api/camera/fragments/<api_key>", methods=["GET"])
@require_auth
def camera_fragments(api_key):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    fragments = []
    for i in range(15):
        fragment_time = datetime.utcnow() - timedelta(minutes=14 - i)
        fragments.append(
            {
                "id": i,
                "start_time": fragment_time.isoformat(),
                "filename": f"frag_{i:03d}.jpg",
            }
        )

    return jsonify(
        {
            "status": "success",
            "fragments": fragments,
            "camera_id": camera.id,
            "download_url": f"/api/camera/download/{api_key}/all",
        }
    ), 200


@app.route("/api/camera/download/<api_key>", methods=["GET"])
@require_auth
def camera_download(api_key):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    socketio.emit(
        "download_requested",
        {"camera_id": camera.id, "camera_name": camera.name},
        room=f"camera_{camera.id}",
    )

    return jsonify(
        {"status": "success", "message": "Download request sent to camera"}
    ), 200


@app.route("/api/camera/download/<api_key>/<int:fragment_id>", methods=["GET"])
@require_auth
def camera_fragment(api_key, fragment_id):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    return jsonify(
        {
            "status": "success",
            "message": f"Fragment {fragment_id} request sent",
            "fragment_id": fragment_id,
            "fragment_url": f"/fragments/frag_{fragment_id:03d}.jpg",
        }
    ), 200


@app.route("/api/camera/download/<api_key>/all", methods=["GET"])
@require_auth
def camera_download_all(api_key):
    camera = Camera.query.filter_by(api_key=api_key, user_id=session["user_id"]).first()
    if not camera:
        return jsonify({"status": "error", "message": "Camera not found"}), 404

    socketio.emit(
        "download_requested",
        {"camera_id": camera.id, "camera_name": camera.name, "action": "download_all"},
        room=f"camera_{camera.id}",
    )

    fragment_urls = []
    for i in range(15):
        fragment_urls.append({"id": i, "url": f"/api/camera/download/{api_key}/{i}"})

    return jsonify(
        {
            "status": "success",
            "message": "Download request sent",
            "fragments": fragment_urls,
            "estimated_size": "~50MB (15 minutes)",
        }
    ), 200


@socketio.on("connect")
def handle_connect():
    logger.info(f"Client connected: {request.sid}")


@socketio.on("disconnect")
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on("esp32_register")
def handle_esp32_register(data):
    try:
        api_key = data.get("api_key")
        if not api_key:
            emit("error", {"message": "Missing api_key"})
            return

        camera = Camera.query.filter_by(api_key=api_key).first()
        if not camera:
            emit("error", {"message": "Invalid api_key"})
            return

        CAMERA_SOCKETS[camera.id] = request.sid
        join_room(f"camera_{camera.id}")

        emit(
            "registered",
            {
                "camera_id": camera.id,
                "config": camera.get_config(),
                "stream_auto_mode": camera.stream_auto_mode,
                "recording_enabled": camera.recording_enabled,
                "is_streaming": camera.is_streaming,
            },
        )
        logger.info(f"ESP32 registered: camera {camera.id} ({camera.name})")
    except Exception as e:
        logger.error(f"ESP32 registration error: {e}")


@socketio.on("client_watch")
def handle_client_watch(data):
    try:
        camera_id = data.get("camera_id")
        if camera_id:
            join_room(f"camera_{camera_id}")
            camera = Camera.query.get(camera_id)
            if camera:
                join_room(f"user_{camera.user_id}")
            emit("watching", {"camera_id": camera_id})
    except Exception as e:
        logger.error(f"Client watch error: {e}")


ADMIN_MASTER_KEY = os.environ.get(
    "MASTER_KEY", os.environ.get("ADMIN_MASTER_KEY", "master_key_pcs_2024")
)


@app.route("/api/admin/users", methods=["GET", "POST"])
@limiter.limit("30 per minute")
@require_admin_auth
def api_admin_users():
    if request.method == "GET":
        users = User.query.all()
        users_list = [
            {
                "id": u.id,
                "username": u.username,
                "created_at": u.created_at.strftime("%Y-%m-%d %H:%M"),
                "subscription_end": u.subscription_end.strftime("%Y-%m-%d")
                if u.subscription_end
                else "Unlimited",
                "subscription_mode": getattr(u, "subscription_mode", "standard")
                or "standard",
                "max_cameras": getattr(u, "max_cameras", 3) or 3,
                "max_blacklist": getattr(u, "max_blacklist", 50)
                if getattr(u, "max_blacklist", 50) is not None
                else 50,
                "features": json.loads(getattr(u, "features_json", "[]") or "[]"),
                "admin_notes": getattr(u, "admin_notes", "") or "",
            }
            for u in users
        ]
        return jsonify({"status": "success", "users": users_list}), 200

    if request.method == "POST":
        data = request.json
        if not data or "username" not in data or "password" not in data:
            return jsonify(
                {"status": "error", "message": "Missing username or password"}
            ), 400

        username = data["username"]
        password = data["password"]

        if User.query.filter_by(username=username).first():
            return jsonify(
                {"status": "error", "message": f"User '{username}' already exists"}
            ), 400

        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        return jsonify(
            {"status": "success", "message": f"User '{username}' created"}
        ), 201

    return jsonify({"status": "error", "message": "Method not allowed"}), 405


@app.route("/api/admin/users/<username>", methods=["DELETE"])
@limiter.limit("30 per minute")
@require_admin_auth
def api_admin_user_delete(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    db.session.delete(user)
    db.session.commit()
    return jsonify({"status": "success", "message": f"User '{username}' deleted"}), 200


@app.route("/api/admin/users/<username>/subscription", methods=["POST"])
@limiter.limit("30 per minute")
@require_admin_auth
def api_admin_user_sub(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    data = request.json or {}
    days = data.get("days", 0)
    mode = data.get("mode")
    max_cameras = data.get("max_cameras")
    max_blacklist = data.get("max_blacklist")
    features = data.get("features")
    admin_notes = data.get("admin_notes")

    if days:
        if not user.subscription_end or user.subscription_end < datetime.utcnow():
            user.subscription_end = datetime.utcnow() + timedelta(days=days)
        else:
            user.subscription_end = user.subscription_end + timedelta(days=days)

    if mode:
        user.subscription_mode = mode
    if max_cameras is not None:
        user.max_cameras = int(max_cameras)
    if max_blacklist is not None:
        user.max_blacklist = int(max_blacklist)
    if features is not None:
        user.features_json = json.dumps(features)
    if admin_notes is not None:
        user.admin_notes = admin_notes

    db.session.commit()

    msg_parts = []
    if days:
        msg_parts.append(f"Valid until {user.subscription_end.strftime('%Y-%m-%d')}")
    if mode:
        msg_parts.append(f"Mode: {mode}")
    if max_cameras is not None:
        msg_parts.append(f"Max cam: {max_cameras}")
    if max_blacklist is not None:
        msg_parts.append(f"Max rules: {max_blacklist if max_blacklist != -1 else '∞'}")
    if features is not None:
        msg_parts.append(f"Features: {len(features)}")

    return jsonify(
        {
            "status": "success",
            "message": " | ".join(msg_parts) if msg_parts else "Updated",
        }
    ), 200


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/setup")
def camera_setup():
    return render_template("camera_setup.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute")
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if user.subscription_end and user.subscription_end < datetime.utcnow():
                flash("Subscription expired.", "error")
                return redirect(url_for("login"))

            session["user_id"] = user.id
            session["username"] = user.username
            return redirect(url_for("dashboard"))
        else:
            flash("Identifiants invalides.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET"])
@require_auth
def dashboard():
    user_id = session["user_id"]
    cameras = Camera.query.filter_by(user_id=user_id).all()
    targets = NotificationTarget.query.filter_by(user_id=user_id).all()
    blacklist = Blacklist.query.filter_by(user_id=user_id).all()
    now = datetime.utcnow()
    for cam in cameras:
        cam.is_online = (
            (now - cam.last_seen).total_seconds() < 600 if cam.last_seen else False
        )

    user = User.query.get(user_id)
    subscription_mode = getattr(user, "subscription_mode", "standard") or "standard"

    return render_template(
        "dashboard.html",
        username=session["username"],
        cameras=cameras,
        targets=targets,
        blacklist=blacklist,
        subscription_mode=subscription_mode,
    )


@app.route("/camera/<int:camera_id>/manage", methods=["GET"])
@require_auth
def camera_management(camera_id):
    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        flash("Caméra non trouvée.", "error")
        return redirect(url_for("dashboard"))

    # Stats for Pro/Emergency
    stats = {}
    if session.get("user_id"):
        user = User.query.get(session["user_id"])
        if user and user.subscription_mode in ["emergency", "pro"]:
            detections = PlateDetection.query.filter_by(camera_id=camera_id).all()
            total = len(detections)
            if total > 0:
                stats = {
                    "total": total,
                    "by_type": {},
                    "by_color": {},
                    "threats": len([d for d in detections if d.is_threat]),
                }
                for d in detections:
                    # Translate 'unknown' to 'Non-reconnu'
                    v_type = (
                        "Non-reconnu" if d.vehicle_type == "unknown" else d.vehicle_type
                    )
                    v_color = (
                        "Non-reconnu"
                        if d.vehicle_color == "unknown"
                        else d.vehicle_color
                    )

                    stats["by_type"][v_type] = stats["by_type"].get(v_type, 0) + 1
                    stats["by_color"][v_color] = stats["by_color"].get(v_color, 0) + 1

    return render_template("camera_management.html", camera=camera, stats=stats)


@app.route("/camera/<int:camera_id>", methods=["GET"])
@require_auth
def camera_view(camera_id):
    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        flash("Caméra non trouvée.", "error")
        return redirect(url_for("dashboard"))

    cameras = Camera.query.filter_by(user_id=session["user_id"]).all()
    now = datetime.utcnow()
    for cam in cameras:
        cam.is_online = (
            (now - cam.last_seen).total_seconds() < 600 if cam.last_seen else False
        )

    return render_template(
        "camera_view.html", camera=camera, cameras=cameras, username=session["username"]
    )


@app.route("/dashboard/update_config", methods=["POST"])
@require_auth
def update_config():
    flash("Configuration mise à jour.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/update_camera_gps/<int:camera_id>", methods=["POST"])
@require_auth
def update_camera_gps(camera_id):
    camera = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if not camera:
        flash("Caméra non trouvée.", "error")
        return redirect(url_for("dashboard"))

    lat = request.form.get("lat")
    lng = request.form.get("lng")

    if lat and lng:
        try:
            camera.lat = float(lat)
            camera.lng = float(lng)
            db.session.commit()
            flash(f"Position de '{camera.name}' mise à jour!", "success")
        except ValueError:
            flash("Coordonnées GPS invalides.", "error")
    else:
        flash("Veuillez entrer des coordonnées valides.", "error")

    return redirect(url_for("dashboard"))


@app.route("/dashboard/trigger_manual_alert", methods=["POST"])
@require_auth
def trigger_manual_alert():
    user_id = session["user_id"]
    msg = request.form.get("message", "Alerte manuelle déclenchée depuis le dashboard.")
    send_alert(user_id, f"🚨 ALERTE MANUELLE 🚨\n{msg}")
    flash("Alerte envoyée.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/add_target", methods=["POST"])
@require_auth
def add_target():
    user_id = session["user_id"]
    user = User.query.get(user_id)

    count = NotificationTarget.query.filter_by(user_id=user_id).count()
    if count >= get_max_notifications(user):
        flash(
            f"Limite de canaux atteinte ({get_max_notifications(user)} max).", "error"
        )
        return redirect(url_for("dashboard"))

    name = request.form.get("name")
    platform = request.form.get("platform")

    if name and platform:
        new_target = NotificationTarget(user_id=user_id, name=name, platform=platform)
        if platform == "signal":
            signal_url = request.form.get("signal_url")
            if signal_url:
                new_target.api_key = signal_url
            else:
                new_target.phone_number = request.form.get("phone")
                new_target.api_key = request.form.get("signal_api_key")
        elif platform == "telegram":
            new_target.bot_token = request.form.get("bot_token")
            new_target.chat_id = request.form.get("chat_id")

        db.session.add(new_target)
        db.session.commit()
        flash("Canal de notification ajouté.", "success")

    return redirect(url_for("dashboard"))


@app.route("/dashboard/add_camera", methods=["POST"])
@require_auth
def add_camera():
    user_id = session["user_id"]

    name = request.form.get("name")
    api_key = request.form.get("api_key")

    if name and api_key:
        if Camera.query.filter_by(api_key=api_key).first():
            flash("Cette clé API est déjà utilisée.", "error")
        else:
            new_cam = Camera(user_id=user_id, name=name, api_key=api_key)
            db.session.add(new_cam)
            db.session.commit()
            flash("Caméra enregistrée avec succès.", "success")

    return redirect(url_for("dashboard"))


@app.route("/dashboard/add_blacklist", methods=["POST"])
@require_auth
def add_blacklist():
    user_id = session["user_id"]

    plate = request.form.get("plate")
    desc = request.form.get("desc")

    vehicle_type = request.form.get("vehicle_type", "any")
    vehicle_color = request.form.get("vehicle_color", "any")
    alert_label = request.form.get("alert_label", "").strip()
    alert_priority = request.form.get("alert_priority", "normal")
    match_plate_val = request.form.get("match_plate", "1")
    match_plate = match_plate_val != "0"

    norm_plate = normalize_plate(plate) if plate else ""

    # Require either a plate (match_plate mode) or vehicle type/color filter
    if not norm_plate and match_plate:
        flash("Veuillez saisir une plaque ou désactiver 'Match par plaque'.", "error")
        return redirect(url_for("dashboard"))

    if norm_plate and match_plate:
        if Blacklist.query.filter_by(
            user_id=user_id, plate_normalized=norm_plate
        ).first():
            flash("Cette plaque est déjà dans la base de données.", "error")
            return redirect(url_for("dashboard"))

    new_bl = Blacklist(
        user_id=user_id,
        plate_normalized=norm_plate,
        description=desc,
        is_police=True,
        vehicle_type=vehicle_type,
        vehicle_color=vehicle_color,
        alert_label=alert_label,
        alert_priority=alert_priority,
        match_plate=match_plate,
    )
    db.session.add(new_bl)
    db.session.commit()
    flash("Règle de surveillance ajoutée.", "success")

    return redirect(url_for("dashboard"))


@app.route("/dashboard/toggle_flash/<int:camera_id>", methods=["POST"])
@require_auth
def toggle_flash(camera_id):
    cam = Camera.query.filter_by(id=camera_id, user_id=session["user_id"]).first()
    if cam:
        cam.flash_detect_enabled = not cam.flash_detect_enabled
        db.session.commit()
    return jsonify({"enabled": cam.flash_detect_enabled if cam else False}), 200


@app.route("/dashboard/del_camera/<int:id>", methods=["POST"])
@require_auth
def del_camera(id):
    cam = Camera.query.filter_by(id=id, user_id=session["user_id"]).first()
    if cam:
        db.session.delete(cam)
        db.session.commit()
        flash("Caméra supprimée.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/del_target/<int:id>", methods=["POST"])
@require_auth
def del_target(id):
    target = NotificationTarget.query.filter_by(
        id=id, user_id=session["user_id"]
    ).first()
    if target:
        db.session.delete(target)
        db.session.commit()
        flash("Canal supprimé.", "success")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/del_blacklist/<int:id>", methods=["POST"])
@require_auth
def del_blacklist(id):
    bl = Blacklist.query.filter_by(id=id, user_id=session["user_id"]).first()
    if bl:
        db.session.delete(bl)
        db.session.commit()
        flash("Plaque effacée.", "success")
    return redirect(url_for("dashboard"))


# ──────────────────────────────────────────────────────────────────────────────
# PCS-AI — API endpoints pour consommation par IA externe
# Auth : X-API-Key = camera api_key  OU  X-User-Key = user session key (futur)
# Ces endpoints sont conçus pour être stables — ne pas casser la compatibilité.
# ──────────────────────────────────────────────────────────────────────────────


def _require_user_api_key(f):
    """Vérifie X-API-Key appartenant à une caméra — identifie l'utilisateur."""

    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key")
        if not key:
            return jsonify({"error": "Missing X-API-Key"}), 401
        camera = Camera.query.filter_by(api_key=key).first()
        if not camera:
            return jsonify({"error": "Invalid API key"}), 401
        request.ai_user_id = camera.user_id
        return f(*args, **kwargs)

    return decorated


@app.route("/api/ai/summaries", methods=["GET"])
@_require_user_api_key
def ai_get_summaries():
    """
    Retourne les résumés de caméra non encore traités par l'IA.
    Query params :
      since=<ISO datetime>   — ne retourner que les résumés créés après cette date
      camera_id=<int>        — filtrer par caméra
      limit=<int>            — max résultats (défaut 50)
      unprocessed_only=1     — seulement ai_processed=False
    """
    user_id = request.ai_user_id
    since_str = request.args.get("since")
    camera_id = request.args.get("camera_id", type=int)
    limit = min(request.args.get("limit", 50, type=int), 200)
    unprocessed_only = request.args.get("unprocessed_only", "0") == "1"

    q = CameraSummary.query.filter_by(user_id=user_id)
    if camera_id:
        q = q.filter_by(camera_id=camera_id)
    if since_str:
        try:
            since_dt = datetime.fromisoformat(
                since_str.replace("Z", "+00:00").replace("+00:00", "")
            )
            q = q.filter(CameraSummary.created_at > since_dt)
        except ValueError:
            return jsonify({"error": "Invalid 'since' format, use ISO 8601"}), 400
    if unprocessed_only:
        q = q.filter_by(ai_processed=False)

    summaries = q.order_by(CameraSummary.created_at.asc()).limit(limit).all()

    return jsonify(
        {
            "count": len(summaries),
            "summaries": [
                {
                    "id": s.id,
                    "camera_id": s.camera_id,
                    "period_start": s.period_start.isoformat(),
                    "period_end": s.period_end.isoformat(),
                    "created_at": s.created_at.isoformat(),
                    "ai_processed": s.ai_processed,
                    "ai_response": s.ai_response or "",
                    "data": json.loads(s.summary_json),
                }
                for s in summaries
            ],
        }
    ), 200


@app.route("/api/ai/latest", methods=["GET"])
@_require_user_api_key
def ai_get_latest():
    """Retourne le dernier résumé pour chaque caméra de l'utilisateur."""
    user_id = request.ai_user_id
    cameras = Camera.query.filter_by(user_id=user_id).all()
    result = []
    for cam in cameras:
        s = (
            CameraSummary.query.filter_by(camera_id=cam.id)
            .order_by(CameraSummary.created_at.desc())
            .first()
        )
        if s:
            result.append(
                {
                    "camera_id": cam.id,
                    "camera_name": cam.name,
                    "summary_id": s.id,
                    "created_at": s.created_at.isoformat(),
                    "ai_processed": s.ai_processed,
                    "data": json.loads(s.summary_json),
                }
            )
    return jsonify({"cameras": result}), 200


@app.route("/api/ai/summaries/<int:summary_id>/response", methods=["POST"])
@_require_user_api_key
def ai_post_response(summary_id):
    """
    L'IA externe écrit son analyse pour un résumé.
    Body JSON : {"response": "...", "anomalies": [...], "actions": [...]}
    """
    user_id = request.ai_user_id
    s = CameraSummary.query.filter_by(id=summary_id, user_id=user_id).first()
    if not s:
        return jsonify({"error": "Summary not found"}), 404

    data = request.json or {}
    s.ai_response = data.get("response", "")
    s.ai_processed = True

    # Optionnel : enrichir le summary_json avec les anomalies/actions
    if "anomalies" in data or "actions" in data:
        try:
            existing = json.loads(s.summary_json)
            if "anomalies" in data:
                existing["anomalies"] = data["anomalies"]
            if "actions" in data:
                existing["ai_actions"] = data["actions"]
            s.summary_json = json.dumps(existing)
        except Exception:
            pass

    db.session.commit()
    return jsonify({"status": "ok", "summary_id": s.id}), 200


@app.route("/api/ai/config", methods=["GET", "POST"])
@_require_user_api_key
def ai_config():
    """Lire / écrire la config IA de l'utilisateur."""
    user_id = request.ai_user_id
    cfg = AIConfig.query.filter_by(user_id=user_id).first()

    if request.method == "GET":
        if not cfg:
            return jsonify({"configured": False, "summary_interval": 60}), 200
        return jsonify(
            {
                "configured": True,
                "provider": cfg.provider,
                "webhook_url": cfg.webhook_url,
                "summary_interval": cfg.summary_interval,
                "ai_enabled": cfg.ai_enabled,
            }
        ), 200

    data = request.json or {}
    if not cfg:
        cfg = AIConfig(user_id=user_id)
        db.session.add(cfg)
    if "provider" in data:
        cfg.provider = data["provider"]
    if "webhook_url" in data:
        cfg.webhook_url = data["webhook_url"]
    if "summary_interval" in data:
        cfg.summary_interval = max(30, int(data["summary_interval"]))  # min 30s
    if "ai_enabled" in data:
        cfg.ai_enabled = bool(data["ai_enabled"])
    db.session.commit()
    return jsonify({"status": "ok", "summary_interval": cfg.summary_interval}), 200


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
