import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import requests
import cv2
import os
import threading
import time
from PIL import Image, ImageTk
from urllib.parse import quote_plus

DEFAULT_API_URL = "https://Wlansolo.pythonanywhere.com"
DEFAULT_API_KEY = "secret123"


class VigilanceSimulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("СЛЕДИ (SLEDI) - Hardware Simulator")
        self.root.geometry("950x750")
        self.root.configure(bg="#050505")

        self.api_url_var = tk.StringVar(value=DEFAULT_API_URL)
        self.api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self.camera_index_var = tk.StringVar()
        self.signal_alert_url_var = tk.StringVar(value="")
        self.emulate_gps_var = tk.BooleanVar(value=True)
        self.emulate_shock_var = tk.BooleanVar(value=False)
        self.emulate_blue_light_var = tk.BooleanVar(value=False)
        self.force_plate_var = tk.StringVar(value="")

        self.cap = None
        self.is_running = False
        self.available_cameras = []

        self.config = {
            "interval_capture_ms": 200,
            "stream_fps": 5,
            "loop_fps": 10,
            "jpeg_quality": 60,
        }
        self.last_stream_time = 0
        self.stream_interval_ms = 200
        self.loop_interval_ms = 100
        self.current_mode = "off"

        self.setup_ui()
        self.find_cameras()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="URL Serveur:").grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        tk.Entry(
            top_frame,
            textvariable=self.api_url_var,
            width=60,
            bg="#000",
            fg="#0f0",
            insertbackground="#0f0",
        ).grid(row=0, column=1, columnspan=2, padx=10, pady=2)

        ttk.Label(top_frame, text="Clé API (ID):").grid(
            row=1, column=0, sticky=tk.W, pady=2
        )
        tk.Entry(
            top_frame,
            textvariable=self.api_key_var,
            width=40,
            bg="#000",
            fg="#0f0",
            insertbackground="#0f0",
        ).grid(row=1, column=1, sticky=tk.W, padx=10, pady=2)

        ttk.Label(top_frame, text="URL Alerte Signal:").grid(
            row=2, column=0, sticky=tk.W, pady=2
        )
        tk.Entry(
            top_frame,
            textvariable=self.signal_alert_url_var,
            width=60,
            bg="#000",
            fg="#ffff00",
            insertbackground="#ffff00",
        ).grid(row=2, column=1, columnspan=2, padx=10, pady=2)

        ttk.Label(top_frame, text="Caméra:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.cam_combo = ttk.Combobox(
            top_frame, textvariable=self.camera_index_var, state="readonly", width=40
        )
        self.cam_combo.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)

        self.btn_toggle_cam = tk.Button(
            top_frame,
            text="▶ DÉMARRER CAMÉRA",
            bg="#0f0",
            fg="#000",
            font=("Courier New", 10, "bold"),
            command=self.toggle_camera,
        )
        self.btn_toggle_cam.grid(row=3, column=2, padx=10)

        self.btn_reload_config = tk.Button(
            top_frame,
            text="🔄 Reload Config",
            bg="#059669",
            fg="white",
            font=("Courier New", 9),
            command=self.reload_config,
        )
        self.btn_reload_config.grid(row=4, column=2, padx=10, pady=5)

        self.config_frame = tk.Frame(self.root, bg="#1a1a1a", padx=10, pady=5)
        self.config_frame.pack(fill=tk.X)
        self.config_label = tk.Label(
            self.config_frame,
            text="Config Serveur: Non chargée",
            bg="#1a1a1a",
            fg="#fbbf24",
            font=("Courier New", 10),
        )
        self.config_label.pack()

        sim_frame = ttk.Frame(self.root, padding=5)
        sim_frame.pack(fill=tk.X)

        tk.Checkbutton(
            sim_frame,
            text="Émuler GPS",
            variable=self.emulate_gps_var,
            bg="#050505",
            fg="#0f0",
            selectcolor="#000",
            activebackground="#050505",
            activeforeground="#0f0",
        ).pack(side=tk.LEFT, padx=10)
        tk.Checkbutton(
            sim_frame,
            text="Émuler Choc",
            variable=self.emulate_shock_var,
            bg="#050505",
            fg="#ff3333",
            selectcolor="#000",
            activebackground="#050505",
            activeforeground="#ff3333",
        ).pack(side=tk.LEFT, padx=10)
        tk.Checkbutton(
            sim_frame,
            text="Gyrophare Bleu",
            variable=self.emulate_blue_light_var,
            bg="#050505",
            fg="#3399ff",
            selectcolor="#000",
            activebackground="#050505",
            activeforeground="#3399ff",
        ).pack(side=tk.LEFT, padx=10)

        tk.Label(sim_frame, text="Forcer Plaque:", bg="#050505", fg="#0f0").pack(
            side=tk.LEFT, padx=(20, 5)
        )
        tk.Entry(
            sim_frame,
            textvariable=self.force_plate_var,
            width=15,
            bg="#000",
            fg="#ff3333",
            insertbackground="#ff3333",
        ).pack(side=tk.LEFT)

        self.video_frame = tk.Frame(
            self.root, bg="#111", width=640, height=480, bd=2, relief=tk.SUNKEN
        )
        self.video_frame.pack(pady=10)
        self.video_label = tk.Label(
            self.video_frame,
            bg="#111",
            text="FLUX VIDÉO HORS LIGNE",
            fg="#555",
            font=("Courier New", 16),
        )
        self.video_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        bottom_frame = ttk.Frame(self.root, padding=10)
        bottom_frame.pack(fill=tk.BOTH, expand=True)
        self.btn_capture = tk.Button(
            bottom_frame,
            text="📸 CAPTURER ET ANALYSER (ESPACE)",
            bg="#0f0",
            fg="#000",
            font=("Courier New", 12, "bold"),
            state=tk.DISABLED,
            command=self.capture_and_send,
        )
        self.btn_capture.pack(fill=tk.X, pady=(0, 10))
        self.log_area = scrolledtext.ScrolledText(
            bottom_frame,
            bg="#000",
            fg="#0f0",
            font=("Courier New", 10),
            height=8,
            bd=1,
            relief=tk.SOLID,
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)
        self.log("Système initialisé. Prêt.")
        self.root.bind(
            "<space>",
            lambda event: self.capture_and_send() if self.is_running else None,
        )

    def log(self, message):
        self.log_area.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_area.see(tk.END)

    def find_cameras(self):
        self.log("Recherche des caméras...")
        self.available_cameras = []
        for i in range(5):
            cap = (
                cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if os.name == "nt"
                else cv2.VideoCapture(i)
            )
            if cap and cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.available_cameras.append(f"Caméra {i} ({w}x{h})")
                cap.release()
        if self.available_cameras:
            self.cam_combo["values"] = self.available_cameras
            self.cam_combo.current(0)
            self.log(f"{len(self.available_cameras)} caméra(s) détectée(s).")
        else:
            self.cam_combo["values"] = ["Aucune caméra détectée"]
            self.cam_combo.current(0)
            self.log("❌ Aucune caméra trouvée.")

    def toggle_camera(self):
        if self.is_running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        selection = self.camera_index_var.get()
        if not selection or selection == "Aucune caméra détectée":
            self.log("❌ Sélectionnez une caméra valide.")
            return
        cam_index = int(selection.split(" ")[1])
        self.cap = (
            cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
            if os.name == "nt"
            else cv2.VideoCapture(cam_index)
        )
        if not self.cap.isOpened():
            self.log(f"❌ Impossible d'ouvrir la caméra {cam_index}.")
            return
        self.is_running = True
        self.btn_toggle_cam.config(text="⏹ ARRÊTER CAMÉRA", bg="#ff3333", fg="#fff")
        self.btn_capture.config(state=tk.NORMAL)
        self.log(f"✅ Caméra {cam_index} active.")
        self.fetch_config()
        self.update_video_feed()

    def stop_camera(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.btn_toggle_cam.config(text="▶ DÉMARRER CAMÉRA", bg="#0f0", fg="#000")
        self.btn_capture.config(state=tk.DISABLED)
        self.video_label.config(image="", text="FLUX VIDÉO HORS LIGNE")
        self.log("Caméra arrêtée.")

    def fetch_config(self):
        api_url = self.api_url_var.get().rstrip("/")
        api_key = self.api_key_var.get()
        try:
            res = requests.get(
                f"{api_url}/ping", params={"api_key": api_key}, timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                self.config = data.get("config", self.config)
                self.current_mode = data.get("stream_auto_mode", "off")
                stream_fps = self.config.get("stream_fps", 5)
                loop_fps = self.config.get("loop_fps", 10)
                self.stream_interval_ms = (
                    int(1000 / stream_fps) if stream_fps > 0 else 200
                )
                self.loop_interval_ms = int(1000 / loop_fps) if loop_fps > 0 else 100
                self.config_label.config(
                    text=f"Config: Stream={stream_fps}fps Loop={loop_fps}fps | Quality={self.config.get('jpeg_quality', 60)} | Mode={data.get('stream_auto_mode', 'N/A')}",
                    fg="#22c55e",
                )
                self.log(
                    f"✅ Config chargée: Stream={stream_fps}fps, Loop={loop_fps}fps, Quality={self.config.get('jpeg_quality', 60)}"
                )
            else:
                self.config_label.config(text="Config: Erreur chargement", fg="#ef4444")
                self.log(f"⚠️ Config: Erreur {res.status_code}")
        except Exception as e:
            self.config_label.config(text="Config: Hors ligne", fg="#fbbf24")
            self.log(f"⚠️ Config: Impossible ({e})")

    def update_video_feed(self):
        if self.is_running and self.cap:
            ret, frame = self.cap.read()
            if ret:
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                img.thumbnail((640, 480))
                imgtk = ImageTk.PhotoImage(image=img)
                self.video_label.imgtk = imgtk
                self.video_label.config(image=imgtk)

                current_time_ms = time.time() * 1000

                interval = (
                    self.stream_interval_ms
                    if self.current_mode in ["stream", "motion"]
                    else self.loop_interval_ms
                )

                if current_time_ms - self.last_stream_time >= interval:
                    self.last_stream_time = current_time_ms
                    self.send_background_telemetry(frame)

            self.root.after(10, self.update_video_feed)

    def reload_config(self):
        self.fetch_config()

    def send_background_telemetry(self, frame):
        data_payload = {"api_key": self.api_key_var.get()}
        if self.emulate_gps_var.get():
            if not hasattr(self, "sim_lat"):
                self.sim_lat, self.sim_lng, self.sim_speed = 48.8566, 2.3522, 30.0
            else:
                import random

                self.sim_lat += random.uniform(-0.0001, 0.0001)
                self.sim_lng += random.uniform(-0.0001, 0.0001)
                self.sim_speed = random.uniform(20.0, 50.0)
            data_payload.update(
                {
                    "lat": str(self.sim_lat),
                    "lng": str(self.sim_lng),
                    "speed": str(self.sim_speed),
                }
            )
        data_payload["shock"] = "1" if self.emulate_shock_var.get() else "0"
        data_payload["blue_light"] = "1" if self.emulate_blue_light_var.get() else "0"

        jpeg_quality = self.config.get("jpeg_quality", 60)
        ret_enc, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if ret_enc:
            stream_url = f"{self.api_url_var.get().rstrip('/')}/stream_upload"

            def send_bg():
                try:
                    requests.post(
                        stream_url,
                        files={"image": buffer.tobytes()},
                        data=data_payload,
                        timeout=5,
                    )
                except:
                    pass

            threading.Thread(target=send_bg, daemon=True).start()

    def capture_and_send(self):
        if not self.is_running or not self.cap:
            return
        ret, frame = self.cap.read()
        if not ret:
            self.log("❌ Erreur lecture image.")
            return
        jpeg_quality = self.config.get("jpeg_quality", 80)
        ret_enc, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if not ret_enc:
            self.log("❌ Erreur encodage image.")
            return
        self.log("🚀 Envoi des données...")
        threading.Thread(
            target=self.send_request_to_server, args=(buffer.tobytes(),), daemon=True
        ).start()

    def send_request_to_server(self, image_bytes):
        api_url = self.api_url_var.get().rstrip("/")
        api_key = self.api_key_var.get()
        data = {"api_key": api_key}
        if self.emulate_gps_var.get() and hasattr(self, "sim_lat"):
            data.update(
                {
                    "lat": str(self.sim_lat),
                    "lng": str(self.sim_lng),
                    "speed": str(self.sim_speed),
                }
            )
        if self.force_plate_var.get():
            data["force_plate"] = self.force_plate_var.get().strip()
        data["blue_light"] = "1" if self.emulate_blue_light_var.get() else "0"

        try:
            res = requests.post(
                api_url, files={"image": image_bytes}, data=data, timeout=10
            ).json()
            if res.get("threat"):
                self.log(f"🚨 MENACE: {res.get('plate')}")
                self.trigger_local_alert(res)
            else:
                self.log(f"✅ Analyse: {res.get('plate') or 'Aucune plaque'}")
        except Exception as e:
            self.log(f"❌ ERREUR: {e}")

    def trigger_local_alert(self, threat_data):
        base_url = self.signal_alert_url_var.get().strip()
        if not base_url:
            self.log("🔔 Alerte locale ignorée: URL non configurée.")
            return
        plate = threat_data.get("plate")
        message_parts = [f"🚨 ALERTE: {plate}"]
        if self.emulate_gps_var.get() and hasattr(self, "sim_lat"):
            message_parts.append(
                f"Lieu: https://www.google.com/maps?q={self.sim_lat},{self.sim_lng}"
            )
        if self.emulate_blue_light_var.get():
            message_parts.append("🔥 GYROPHARE BLEU!")
        message = "\n".join(message_parts)
        encoded_message = quote_plus(message)
        final_url = (
            f"{base_url}&text={encoded_message}"
            if "&text=" in base_url
            else f"{base_url}?text={encoded_message}"
        )

        def send_alert():
            try:
                requests.get(final_url, timeout=5)
                self.log("✅ Alerte envoyée!")
            except:
                self.log("❌ Échec alerte")

        threading.Thread(target=send_alert, daemon=True).start()

    def on_closing(self):
        if self.is_running:
            self.stop_camera()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = VigilanceSimulatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
