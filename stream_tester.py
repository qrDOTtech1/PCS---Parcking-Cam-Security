import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import requests
import time
import threading
import warnings
from PIL import Image, ImageTk

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


class StreamTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCS Stream Tester")
        self.root.geometry("800x600")
        self.root.configure(bg="#0f172a")

        self.session = requests.Session()
        self.cap = None
        self.is_running = False
        self.available_cameras = []
        self.frame_count = 0
        self.start_time = time.time()
        self.last_fps_update = time.time()
        self.current_fps = 0

        self.config = {"stream_fps": 30, "loop_fps": 15, "jpeg_quality": 70}
        self.stream_interval_ms = 33
        self.last_stream_time = 0

        self.setup_ui()
        self.find_cameras()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        top_frame = tk.Frame(self.root, bg="#1e293b", pady=10)
        top_frame.pack(fill=tk.X, padx=10)

        tk.Label(
            top_frame,
            text="PCS Stream Tester",
            font=("Arial", 16, "bold"),
            fg="#3b82f6",
            bg="#1e293b",
        ).pack()

        config_frame = tk.Frame(self.root, bg="#1e293b", padx=10, pady=5)
        config_frame.pack(fill=tk.X)

        tk.Label(config_frame, text="Server URL:", fg="#f8fafc", bg="#1e293b").grid(
            row=0, column=0, sticky=tk.W
        )
        self.url_entry = ttk.Entry(config_frame, width=40)
        self.url_entry.insert(0, "https://your-pcs-app.up.railway.app")
        self.url_entry.grid(row=0, column=1, padx=5, pady=2)

        tk.Label(config_frame, text="API Key:", fg="#f8fafc", bg="#1e293b").grid(
            row=1, column=0, sticky=tk.W
        )
        self.api_key_entry = ttk.Entry(config_frame, width=40, show="*")
        self.api_key_entry.grid(row=1, column=1, padx=5, pady=2)

        tk.Label(config_frame, text="Camera:", fg="#f8fafc", bg="#1e293b").grid(
            row=2, column=0, sticky=tk.W
        )
        self.camera_var = tk.StringVar()
        self.camera_combo = ttk.Combobox(
            config_frame, textvariable=self.camera_var, state="readonly", width=15
        )
        self.camera_combo.grid(row=2, column=1, padx=5, pady=2, sticky=tk.W)
        self.camera_combo.bind("<<ComboboxSelected>>", self.on_camera_change)

        btn_cam_refresh = tk.Button(
            config_frame,
            text="↻",
            bg="#475569",
            fg="white",
            command=self.find_cameras,
            width=3,
        )
        btn_cam_refresh.grid(row=2, column=2, padx=2)

        self.btn_toggle = tk.Button(
            config_frame,
            text="▶ START",
            bg="#22c55e",
            fg="white",
            font=("Arial", 10, "bold"),
            command=self.toggle_stream,
            width=12,
        )
        self.btn_toggle.grid(row=0, column=2, rowspan=2, padx=10)

        btn_reload = tk.Button(
            config_frame,
            text="Reload Config",
            bg="#3b82f6",
            fg="white",
            command=self.reload_config,
        )
        btn_reload.grid(row=3, column=1, pady=5, sticky=tk.W)

        self.fps_label = tk.Label(
            config_frame, text="FPS: 0 | Sent: 0", fg="#22c55e", bg="#1e293b"
        )
        self.fps_label.grid(row=3, column=2, pady=5)

        video_frame = tk.Frame(self.root, bg="#000")
        video_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.video_label = tk.Label(
            video_frame, text="STREAM OFFLINE", bg="#000", fg="#666", font=("Arial", 20)
        )
        self.video_label.pack(fill=tk.BOTH, expand=True)

        log_frame = tk.Frame(self.root, bg="#0f172a")
        log_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(log_frame, text="Log:", fg="#94a3b8", bg="#0f172a").pack(anchor=tk.W)

        self.log_text = tk.Text(
            log_frame, height=6, bg="#1e293b", fg="#f8fafc", font=("Consolas", 9)
        )
        self.log_text.pack(fill=tk.X)

        scrollbar = tk.Scrollbar(self.log_text)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)

    def find_cameras(self):
        self.available_cameras = []
        for i in range(5):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                self.available_cameras.append(i)
                cap.release()

        if self.available_cameras:
            self.camera_combo["values"] = [
                f"Camera {i}" for i in self.available_cameras
            ]
            self.camera_combo.current(0)
            self.log(f"Cameras found: {self.available_cameras}")
        else:
            self.camera_combo["values"] = ["No camera"]
            self.log("ERROR: No cameras found!")

    def on_camera_change(self, event=None):
        if self.is_running:
            self.toggle_stream()

    def reload_config(self):
        url = self.url_entry.get().rstrip("/")
        api_key = self.api_key_entry.get()
        if not api_key:
            self.log("ERROR: API Key required")
            return

        try:
            res = self.session.get(
                f"{url}/ping", params={"api_key": api_key}, timeout=10, verify=False
            )
            if res.status_code == 200:
                data = res.json()
                self.config = data.get("config", self.config)
                stream_fps = self.config.get("stream_fps", 30)
                self.stream_interval_ms = int(1000 / stream_fps)
                self.log(
                    f"Config loaded: Stream {stream_fps}fps, Quality {self.config.get('jpeg_quality', 70)}"
                )
            else:
                self.log(f"Config error: {res.status_code}")
        except Exception as e:
            self.log(f"Config failed: {e}")

    def toggle_stream(self):
        if self.is_running:
            self.stop_stream()
        else:
            self.start_stream()

    def start_stream(self):
        url = self.url_entry.get().rstrip("/")
        api_key = self.api_key_entry.get()

        if not api_key:
            messagebox.showerror("Error", "API Key required!")
            return

        camera_idx = (
            self.available_cameras[self.camera_combo.current()]
            if self.available_cameras
            else 0
        )
        self.cap = cv2.VideoCapture(camera_idx)

        if not self.cap.isOpened():
            messagebox.showerror("Error", "Cannot open camera!")
            return

        self.is_running = True
        self.frame_count = 0
        self.start_time = time.time()
        self.last_fps_update = time.time()

        self.btn_toggle.config(text="■ STOP", bg="#ef4444")
        self.reload_config()
        self.log(f"Streaming started (Camera {camera_idx})")

        self.update_video_feed()

    def stop_stream(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None

        self.btn_toggle.config(text="▶ START", bg="#22c55e")
        self.video_label.config(text="STREAM OFFLINE", bg="#000", fg="#666")
        self.log(f"Streaming stopped. Total frames: {self.frame_count}")

    def update_video_feed(self):
        if not self.is_running or not self.cap:
            return

        ret, frame = self.cap.read()
        if ret:
            frame_small = cv2.resize(frame, (640, 480))
            img = Image.fromarray(cv2.cvtColor(frame_small, cv2.COLOR_BGR2RGB))
            imgtk = ImageTk.PhotoImage(image=img)
            self.video_label.imgtk = imgtk
            self.video_label.config(image=imgtk, bg="#000")

            current_time_ms = time.time() * 1000
            if current_time_ms - self.last_stream_time >= self.stream_interval_ms:
                self.last_stream_time = current_time_ms
                self.send_frame(frame_small)

            elapsed = time.time() - self.last_fps_update
            if elapsed >= 1.0:
                self.current_fps = self.frame_count / elapsed
                self.fps_label.config(
                    text=f"FPS: {self.current_fps:.1f} | Sent: {self.frame_count}"
                )
                self.frame_count = 0
                self.start_time = time.time()
                self.last_fps_update = time.time()

        self.root.after(10, self.update_video_feed)

    def send_frame(self, frame):
        url = self.url_entry.get().rstrip("/")
        api_key = self.api_key_entry.get()

        quality = self.config.get("jpeg_quality", 70)
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])

        def send_bg():
            try:
                r = requests.post(
                    f"{url}/stream_upload",
                    files={"image": buffer.tobytes()},
                    data={"api_key": api_key},
                    timeout=5,
                    verify=False,
                )
            except:
                pass

        threading.Thread(target=send_bg, daemon=True).start()
        self.frame_count += 1


if __name__ == "__main__":
    root = tk.Tk()
    app = StreamTesterApp(root)
    root.mainloop()
