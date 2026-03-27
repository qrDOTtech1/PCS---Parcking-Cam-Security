from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    subscription_end = db.Column(db.DateTime, nullable=True)
    subscription_mode = db.Column(db.String(30), default="standard")
    max_cameras = db.Column(db.Integer, default=3)
    max_blacklist = db.Column(db.Integer, default=50)       # -1 = unlimited
    features_json = db.Column(db.Text, default="[]")        # JSON list of enabled features
    admin_notes = db.Column(db.Text, default="")

    cameras = db.relationship(
        "Camera", backref="owner", lazy=True, cascade="all, delete-orphan"
    )
    blacklists = db.relationship(
        "Blacklist", backref="owner", lazy=True, cascade="all, delete-orphan"
    )
    notification_targets = db.relationship(
        "NotificationTarget", backref="owner", lazy=True, cascade="all, delete-orphan"
    )
    system_configs = db.relationship(
        "SystemConfig", backref="owner", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    api_key = db.Column(db.String(100), unique=True, nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    speed = db.Column(db.Float, nullable=True)
    config_json = db.Column(db.Text, default="{}")
    recording_enabled = db.Column(db.Boolean, default=True)
    stream_auto_mode = db.Column(db.String(20), default="off")
    is_streaming = db.Column(db.Boolean, default=False)

    def get_config(self):
        import json

        try:
            return json.loads(self.config_json)
        except:
            return {
                "interval_capture_ms": 1000,
                "motion_threshold_percent": 10,
                "jpeg_quality": 60,
                "stream_fps": 5,
                "loop_fps": 10,
            }

    def set_config(self, config_dict):
        import json

        self.config_json = json.dumps(config_dict)


class Blacklist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    plate_normalized = db.Column(db.String(20), nullable=True)   # nullable: type/color-only rules
    description = db.Column(db.String(200))
    is_police = db.Column(db.Boolean, default=False)
    # Advanced vehicle filters (emergency / enterprise modes)
    vehicle_type = db.Column(db.String(20), default="any")   # any/car/truck/bus/motorcycle
    vehicle_color = db.Column(db.String(20), default="any")  # any/red/blue/white/black/silver/yellow/green/orange
    alert_label = db.Column(db.String(50), default="")       # e.g. "Police", "SAMU", "Pompiers"
    alert_priority = db.Column(db.String(10), default="normal")  # normal/high/critical
    match_plate = db.Column(db.Boolean, default=True)        # False = type+color only (no plate)


class NotificationTarget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    platform = db.Column(db.String(20), default="signal")
    phone_number = db.Column(db.String(20))
    api_key = db.Column(db.String(100))
    bot_token = db.Column(db.String(100))
    chat_id = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)


class PlateDetection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=False)
    plate_normalized = db.Column(db.String(20), nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_threat = db.Column(db.Boolean, default=False)

    camera = db.relationship("Camera", backref="detections")


class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    key_name = db.Column(db.String(50), nullable=False)
    key_value = db.Column(db.String(255))
