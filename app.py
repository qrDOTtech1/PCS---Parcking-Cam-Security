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
from models import db, User, Camera, Blacklist, NotificationTarget, SystemConfig
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

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.environ.get("SECRET_KEY", "vigilance-super-secret-key-change-in-prod"),
)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///vigilance.db"
)
app.config["MASTER_KEY"] = os.environ.get("MASTER_KEY", "master_key_sledi_2024")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=30,
    ping_interval=10,
)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)

PLATE_RECOGNIZER_URL = os.environ.get(
    "PLATE_RECOGNIZER_URL", "https://api.platerecognizer.com/v1/plate-reader"
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
        if not key or key != os.environ.get("MASTER_KEY", "master_key_sledi_2024"):
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

def get_plate_recognizer_token(user_id):
    config = SystemConfig.query.filter_by(
        user_id=user_id, key_name="plate_recognizer_token"
    ).first()
    if config and config.key_value:
        return config.key_value
    return "YOUR_TOKEN_HERE"


def normalize_plate(plate_str):
    if not plate_str:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(plate_str).upper())


def send_alert(user_id, message):
    targets = NotificationTarget.query.filter_by(user_id=user_id, is_active=True).all()
    encoded_message = urllib.parse.quote(message)
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
        try:
            conn.execute(text("ALTER TABLE camera ADD COLUMN config_json TEXT"))
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
        conn.commit()


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
        image_bytes = file.read()
        token = get_plate_recognizer_token(user_id)
        force_plate = request.form.get("force_plate")

        try:
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "RGBA":
                img = img.convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            image_bytes = buffer.getvalue()
            logger.info(f"[OCR] Image converted to JPEG: {len(image_bytes)} bytes")
        except ImportError:
            logger.warning("[OCR] Pillow not installed, using raw bytes")
        except Exception as e:
            logger.error(f"[OCR] Image conversion error: {e}, using original bytes")

        files = {"upload": ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")}
        headers = {"Authorization": f"Token {token}"}

        try:
            if force_plate:
                data = {"results": [{"plate": force_plate}]}
                time.sleep(0.5)
            elif token == "YOUR_TOKEN_HERE" or not token:
                simulated_plate = random.choice(["AB123CD", "XX999YY", None])
                data = (
                    {"results": [{"plate": simulated_plate}]}
                    if simulated_plate
                    else {"results": []}
                )
                time.sleep(0.5)
            else:
                response = requests.post(
                    PLATE_RECOGNIZER_URL, files=files, headers=headers
                )
                response.raise_for_status()
                data = response.json()

            if data["results"]:
                plate_read = data["results"][0]["plate"]
                normalized_read = normalize_plate(plate_read)

                blacklisted = Blacklist.query.filter_by(
                    user_id=user_id, plate_normalized=normalized_read
                ).first()
                threat = False

                if blacklisted:
                    threat = True
                    alert_msg = f"🚨 ALERTE VIGILANCE 🚨\nVéhicule Suspect!\nPlaque: {normalized_read}\nCaméra: {camera.name}\nRaison: {blacklisted.description}"
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

        except requests.exceptions.RequestException as e:
            logger.error(f"Plate recognition error: {e}")
            return jsonify({"status": "error", "message": "External API Error"}), 502

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
    mjpeg_clients = MJPEG_STREAMS.get(camera_id, {}).get("clients", 0) if camera_id in MJPEG_STREAMS else 0

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
    "MASTER_KEY", os.environ.get("ADMIN_MASTER_KEY", "master_key_sledi_2024")
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

        db.session.add(
            SystemConfig(
                user_id=new_user.id,
                key_name="plate_recognizer_token",
                key_value="YOUR_TOKEN_HERE",
            )
        )
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
    days = data.get("days", 30)

    if not user.subscription_end or user.subscription_end < datetime.utcnow():
        user.subscription_end = datetime.utcnow() + timedelta(days=days)
    else:
        user.subscription_end = user.subscription_end + timedelta(days=days)

    db.session.commit()
    return jsonify(
        {
            "status": "success",
            "message": f"Subscription extended to {user.subscription_end.strftime('%Y-%m-%d')}",
        }
    ), 200


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


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
    pr_token = get_plate_recognizer_token(user_id)

    now = datetime.utcnow()
    for cam in cameras:
        cam.is_online = (
            (now - cam.last_seen).total_seconds() < 600 if cam.last_seen else False
        )

    return render_template(
        "dashboard.html",
        username=session["username"],
        cameras=cameras,
        targets=targets,
        blacklist=blacklist,
        pr_token=pr_token,
    )


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
    user_id = session["user_id"]

    token = request.form.get("pr_token")
    if token:
        config = SystemConfig.query.filter_by(
            user_id=user_id, key_name="plate_recognizer_token"
        ).first()
        if config:
            config.key_value = token
        else:
            db.session.add(
                SystemConfig(
                    user_id=user_id, key_name="plate_recognizer_token", key_value=token
                )
            )
        db.session.commit()
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


@app.route("/dashboard/add_target", methods=["POST"])
@require_auth
def add_target():
    user_id = session["user_id"]

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

    if plate:
        norm_plate = normalize_plate(plate)
        if not Blacklist.query.filter_by(
            user_id=user_id, plate_normalized=norm_plate
        ).first():
            new_bl = Blacklist(
                user_id=user_id,
                plate_normalized=norm_plate,
                description=desc,
                is_police=True,
            )
            db.session.add(new_bl)
            db.session.commit()
            flash("Plaque ajoutée à la base de données.", "success")

    return redirect(url_for("dashboard"))


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


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
