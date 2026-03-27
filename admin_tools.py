import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import requests
import time
import os
import json
from pathlib import Path


def load_master_key():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("MASTER_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


API_BASE_URL = "https://web-production-10852.up.railway.app/api/admin/users"
MASTER_KEY = load_master_key() or "master_key_pcs_2024"
MAX_RETRIES = 3
RETRY_DELAY = 2

# All available feature flags
ALL_FEATURES = [
    ("vehicle_filters",   "Vehicle type/color filters (detect car/truck/bus/motorcycle + color)"),
    ("type_color_only",   "Type+Color rules without plate (e.g. alert on any red truck)"),
    ("priority_alerts",   "Custom alert labels & priority levels (Normal / High / Critical)"),
    ("anpr_history",      "Full ANPR detection history & logs"),
    ("multi_notify",      "Multiple notification targets (Signal, Telegram)"),
    ("gps_tracking",      "GPS position tracking per camera"),
    ("unlimited_blacklist","Unlimited watch rules (no blacklist cap)"),
]

# Preset modes — define all fields
SUBSCRIPTION_MODES = {
    "standard": {
        "label": "Standard",
        "color": "#3b82f6",
        "max_cameras": 3,
        "max_blacklist": 20,
        "days": 30,
        "features": [],
        "desc": "3 cameras, 20 rules, 30 days",
    },
    "pro": {
        "label": "Pro",
        "color": "#8b5cf6",
        "max_cameras": 10,
        "max_blacklist": 100,
        "days": 365,
        "features": ["vehicle_filters", "priority_alerts", "anpr_history", "multi_notify", "gps_tracking"],
        "desc": "10 cameras, 100 rules, 1 year",
    },
    "emergency": {
        "label": "Centre d'Urgence",
        "color": "#ef4444",
        "max_cameras": 50,
        "max_blacklist": 500,
        "days": 365,
        "features": ["vehicle_filters", "type_color_only", "priority_alerts", "anpr_history",
                     "multi_notify", "gps_tracking"],
        "desc": "50 cameras, 500 rules, type/color alerts, 1 year",
    },
    "enterprise": {
        "label": "Enterprise",
        "color": "#f59e0b",
        "max_cameras": 100,
        "max_blacklist": -1,
        "days": 365,
        "features": [f[0] for f in ALL_FEATURES],  # all features
        "desc": "100 cameras, unlimited rules, all features, 1 year",
    },
    "custom": {
        "label": "Custom",
        "color": "#6b7280",
        "max_cameras": None,
        "max_blacklist": None,
        "days": None,
        "features": [],
        "desc": "Configure everything manually",
    },
}


def _make_request(method, url, **kwargs):
    global MASTER_KEY
    kwargs.setdefault("timeout", (10, 60))
    kwargs.setdefault("headers", {"X-Master-Key": MASTER_KEY})
    for attempt in range(MAX_RETRIES):
        try:
            return requests.request(method, url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise


class AdminPCSApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ParkingCamSecurity (PCS) — Admin Control Panel")
        self.root.geometry("900x680")
        self.root.configure(bg="#0f172a")
        self.status_label = None
        self._selected_user_data = {}
        self.setup_ui()
        self.refresh_users()

    # ─── UI SETUP ────────────────────────────────────────────────────────────

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Top bar
        top = tk.Frame(self.root, bg="#1e293b", pady=12)
        top.pack(fill=tk.X)

        tk.Label(top, text="ParkingCamSecurity (PCS) ADMIN", font=("Arial", 15, "bold"),
                 fg="#3b82f6", bg="#1e293b").pack()
        tk.Label(top, text="Remote Client Management", font=("Arial", 9),
                 fg="#94a3b8", bg="#1e293b").pack()
        self.status_label = tk.Label(top, text="", font=("Arial", 9), fg="#fbbf24", bg="#1e293b")
        self.status_label.pack()

        btns = tk.Frame(top, bg="#1e293b")
        btns.pack(pady=4)
        for text, color, cmd in [
            ("Test Connection", "#475569", self.test_connection),
            ("Refresh", "#059669", self.refresh_users),
            ("Server Settings", "#7c3aed", self.server_settings_dialog),
        ]:
            tk.Button(btns, text=text, bg=color, fg="white", relief=tk.FLAT,
                      command=cmd).pack(side=tk.LEFT, padx=4)

        # Create account
        mid = tk.Frame(self.root, bg="#0f172a", pady=8)
        mid.pack(fill=tk.X, padx=16)
        form = tk.Frame(mid, bg="#1e293b", bd=1, relief=tk.SOLID, pady=12, padx=12)
        form.pack(fill=tk.X)
        tk.Label(form, text="Create New Client Account", font=("Arial", 11, "bold"),
                 fg="#f8fafc", bg="#1e293b").grid(row=0, column=0, columnspan=4,
                                                  pady=(0, 8), sticky=tk.W)
        tk.Label(form, text="Username:", fg="#cbd5e1", bg="#1e293b").grid(row=1, column=0, sticky=tk.W)
        self.entry_user = ttk.Entry(form, width=22)
        self.entry_user.grid(row=1, column=1, padx=6)
        tk.Label(form, text="Password:", fg="#cbd5e1", bg="#1e293b").grid(row=1, column=2,
                                                                           sticky=tk.W, padx=(10, 0))
        self.entry_pass = ttk.Entry(form, width=22, show="*")
        self.entry_pass.grid(row=1, column=3, padx=6)
        tk.Button(form, text="Create Account", bg="#2563eb", fg="white",
                  font=("Arial", 10, "bold"), relief=tk.FLAT,
                  command=self.create_user).grid(row=2, column=0, columnspan=4,
                                                  pady=(8, 0), ipadx=10, ipady=2)

        # Client list
        bot = tk.Frame(self.root, bg="#0f172a", pady=8)
        bot.pack(fill=tk.BOTH, expand=True, padx=16)

        hdr = tk.Frame(bot, bg="#0f172a")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Registered Clients", font=("Arial", 12, "bold"),
                 fg="#f8fafc", bg="#0f172a").pack(side=tk.LEFT)
        tk.Button(hdr, text="View Details", bg="#0ea5e9", fg="white", relief=tk.FLAT,
                  command=self.view_client_details).pack(side=tk.RIGHT, padx=4)

        cols = ("id", "username", "mode", "cameras", "rules", "features", "valid_until", "notes")
        self.tree = ttk.Treeview(bot, columns=cols, show="headings", height=8)
        specs = [
            ("id", "ID", 30), ("username", "Username", 110), ("mode", "Mode", 90),
            ("cameras", "Cam", 40), ("rules", "Rules", 45), ("features", "Features", 160),
            ("valid_until", "Valid Until", 90), ("notes", "Notes", 100),
        ]
        for col, heading, width in specs:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, anchor=tk.CENTER if width < 80 else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(8, 4))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # ── Action rows ──────────────────────────────────────────────────────
        # Row 1: Subscription duration
        row1 = tk.Frame(bot, bg="#0f172a")
        row1.pack(fill=tk.X, pady=(0, 3))
        tk.Label(row1, text="+ Days:", fg="#94a3b8", bg="#0f172a",
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 4))
        for days, label, color in [(30, "+30d", "#16a34a"), (90, "+90d", "#15803d"),
                                    (365, "+1yr", "#14532d")]:
            tk.Button(row1, text=label, bg=color, fg="white", font=("Arial", 9, "bold"),
                      relief=tk.FLAT, command=lambda d=days: self.add_subscription(d)
                      ).pack(side=tk.LEFT, padx=2, ipadx=6, ipady=2)
        tk.Button(row1, text="Custom Days…", bg="#475569", fg="white", font=("Arial", 9),
                  relief=tk.FLAT, command=self.custom_days_dialog
                  ).pack(side=tk.LEFT, padx=4, ipadx=6, ipady=2)

        # Row 2: Mode presets
        row2 = tk.Frame(bot, bg="#0f172a")
        row2.pack(fill=tk.X, pady=(0, 3))
        tk.Label(row2, text="Mode:", fg="#94a3b8", bg="#0f172a",
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 4))
        for key, info in SUBSCRIPTION_MODES.items():
            tk.Button(row2, text=info["label"], bg=info["color"], fg="white",
                      font=("Arial", 9, "bold"), relief=tk.FLAT,
                      command=lambda k=key: self.apply_mode(k)
                      ).pack(side=tk.LEFT, padx=2, ipadx=5, ipady=2)

        # Row 3: Quick setters + danger
        row3 = tk.Frame(bot, bg="#0f172a")
        row3.pack(fill=tk.X, pady=(0, 8))
        tk.Button(row3, text="Set Camera Limit…", bg="#d97706", fg="white",
                  font=("Arial", 9, "bold"), relief=tk.FLAT,
                  command=self.camera_limit_dialog).pack(side=tk.LEFT, padx=2, ipadx=6, ipady=2)
        tk.Button(row3, text="Set Rule Limit…", bg="#b45309", fg="white",
                  font=("Arial", 9, "bold"), relief=tk.FLAT,
                  command=self.rule_limit_dialog).pack(side=tk.LEFT, padx=2, ipadx=6, ipady=2)
        tk.Button(row3, text="Edit Features…", bg="#0f766e", fg="white",
                  font=("Arial", 9, "bold"), relief=tk.FLAT,
                  command=self.features_dialog).pack(side=tk.LEFT, padx=2, ipadx=6, ipady=2)
        tk.Button(row3, text="Admin Notes…", bg="#4338ca", fg="white",
                  font=("Arial", 9, "bold"), relief=tk.FLAT,
                  command=self.notes_dialog).pack(side=tk.LEFT, padx=2, ipadx=6, ipady=2)
        tk.Button(row3, text="Delete Client", bg="#dc2626", fg="white",
                  font=("Arial", 9, "bold"), relief=tk.FLAT,
                  command=self.delete_user).pack(side=tk.RIGHT, padx=2, ipadx=8, ipady=2)

    # ─── HELPERS ─────────────────────────────────────────────────────────────

    def _on_select(self, _event):
        sel = self.tree.focus()
        if sel:
            vals = self.tree.item(sel, "values")
            # Try to find full data from last loaded list
            self._selected_user_data = {}

    def _selected_username(self):
        sel = self.tree.focus()
        if not sel:
            messagebox.showwarning("Selection", "Select a client first.")
            return None
        return self.tree.item(sel, "values")[1]

    def _send_sub(self, username, payload):
        self.status_label.config(text="Updating…", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("POST", f"{API_BASE_URL}/{username}/subscription",
                                json=payload)
            data = res.json()
            if res.status_code == 200:
                self.status_label.config(text="Updated!", fg="#22c55e")
                messagebox.showinfo("Success", data.get("message", "Updated."))
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", str(e))

    # ─── SERVER SETTINGS ─────────────────────────────────────────────────────

    def server_settings_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Server Settings")
        dlg.geometry("460x200")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Server URL:", fg="#f8fafc", bg="#1e293b").pack(pady=(14, 3))
        e_url = ttk.Entry(dlg, width=52)
        e_url.insert(0, API_BASE_URL.replace("/api/admin/users", ""))
        e_url.pack()
        tk.Label(dlg, text="Master Key:", fg="#f8fafc", bg="#1e293b").pack(pady=(10, 3))
        e_key = ttk.Entry(dlg, width=52, show="*")
        e_key.pack()
        tk.Label(dlg, text="Session only — not saved to disk",
                 fg="#94a3b8", bg="#1e293b", font=("Arial", 8)).pack(pady=3)
        def save():
            global MASTER_KEY, API_BASE_URL
            k, u = e_key.get().strip(), e_url.get().strip().rstrip("/")
            if k:
                MASTER_KEY = k
            if u:
                API_BASE_URL = f"{u}/api/admin/users"
            self.status_label.config(text="Settings applied", fg="#22c55e")
            dlg.destroy()
        tk.Button(dlg, text="Apply", bg="#2563eb", fg="white", command=save).pack(pady=12)

    # ─── CONNECTION ───────────────────────────────────────────────────────────

    def test_connection(self):
        self.status_label.config(text="Testing…", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("GET", API_BASE_URL)
            if res.status_code in [200, 401]:
                self.status_label.config(text="Server OK", fg="#22c55e")
                messagebox.showinfo("OK", f"Server responding — status {res.status_code}")
            else:
                self.status_label.config(text="Server error", fg="#ef4444")
                messagebox.showwarning("Status", f"HTTP {res.status_code}")
        except Exception as e:
            self.status_label.config(text="Connection failed", fg="#ef4444")
            messagebox.showerror("Failed", str(e))

    # ─── USER LIST ────────────────────────────────────────────────────────────

    def refresh_users(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.status_label.config(text="Loading…", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("GET", API_BASE_URL)
            if res.status_code == 200:
                data = res.json()
                self._all_users = data.get("users", [])
                for u in self._all_users:
                    feats = u.get("features", [])
                    feat_str = ", ".join(feats) if feats else "—"
                    rules = u.get("max_blacklist", 50)
                    rules_str = "∞" if rules == -1 else str(rules)
                    notes = u.get("admin_notes", "") or ""
                    notes_str = notes[:20] + "…" if len(notes) > 20 else notes
                    self.tree.insert("", tk.END, values=(
                        u["id"], u["username"],
                        (u.get("subscription_mode") or "standard").capitalize(),
                        u.get("max_cameras", 3), rules_str, feat_str,
                        u.get("subscription_end", "Unlimited"), notes_str,
                    ))
                self.status_label.config(
                    text=f"Loaded {len(self._all_users)} clients", fg="#22c55e")
            elif res.status_code == 401:
                self.status_label.config(text="Auth error", fg="#ef4444")
                messagebox.showerror("Auth Error", "Invalid MASTER_KEY.")
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", f"HTTP {res.status_code}")
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", str(e))

    def _get_user_data(self, username):
        for u in getattr(self, "_all_users", []):
            if u["username"] == username:
                return u
        return {}

    # ─── CREATE / DELETE ─────────────────────────────────────────────────────

    def create_user(self):
        user, pwd = self.entry_user.get().strip(), self.entry_pass.get().strip()
        if not user or not pwd:
            messagebox.showwarning("Validation", "Username and password required.")
            return
        self.status_label.config(text="Creating…", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("POST", API_BASE_URL, json={"username": user, "password": pwd})
            data = res.json()
            if res.status_code == 201:
                self.status_label.config(text="Created!", fg="#22c55e")
                messagebox.showinfo("Success", f"Client '{user}' created!")
                self.entry_user.delete(0, tk.END)
                self.entry_pass.delete(0, tk.END)
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", str(e))

    def delete_user(self):
        username = self._selected_username()
        if not username:
            return
        if not messagebox.askyesno("Confirm", f"Delete '{username}' and ALL data?"):
            return
        self.status_label.config(text="Deleting…", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("DELETE", f"{API_BASE_URL}/{username}")
            data = res.json()
            if res.status_code == 200:
                self.status_label.config(text="Deleted!", fg="#22c55e")
                messagebox.showinfo("Success", f"'{username}' deleted.")
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown"))
        except Exception as e:
            messagebox.showerror("Network Error", str(e))

    # ─── SUBSCRIPTION ─────────────────────────────────────────────────────────

    def add_subscription(self, days):
        username = self._selected_username()
        if username:
            self._send_sub(username, {"days": days})

    def custom_days_dialog(self):
        username = self._selected_username()
        if not username:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Custom Days — {username}")
        dlg.geometry("280x130")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Days to add:", fg="#f8fafc", bg="#1e293b").pack(pady=(16, 4))
        e = ttk.Entry(dlg, width=14)
        e.insert(0, "180")
        e.pack()
        def apply():
            try:
                days = int(e.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Enter a valid number.")
                return
            dlg.destroy()
            self._send_sub(username, {"days": days})
        tk.Button(dlg, text="Add", bg="#16a34a", fg="white",
                  font=("Arial", 10, "bold"), command=apply).pack(pady=14)

    # ─── MODES ────────────────────────────────────────────────────────────────

    def apply_mode(self, mode_key):
        username = self._selected_username()
        if not username:
            return
        if mode_key == "custom":
            self.full_custom_dialog(username)
            return
        info = SUBSCRIPTION_MODES[mode_key]
        msg = (f"Apply mode '{info['label']}' to '{username}'?\n\n"
               f"  Cameras: {info['max_cameras']}\n"
               f"  Rules:   {'∞' if info['max_blacklist'] == -1 else info['max_blacklist']}\n"
               f"  Days:    +{info['days']}\n"
               f"  Features: {len(info['features'])}\n\n{info['desc']}")
        if not messagebox.askyesno("Confirm Mode", msg):
            return
        payload = {
            "mode": mode_key,
            "max_cameras": info["max_cameras"],
            "max_blacklist": info["max_blacklist"],
            "days": info["days"],
            "features": info["features"],
        }
        self._send_sub(username, payload)

    # ─── QUICK DIALOGS ────────────────────────────────────────────────────────

    def _simple_int_dialog(self, title, label, default, callback, allow_minus_one=False):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("300x140")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text=label, fg="#f8fafc", bg="#1e293b").pack(pady=(16, 4))
        if allow_minus_one:
            tk.Label(dlg, text="(-1 = unlimited)", fg="#94a3b8", bg="#1e293b",
                     font=("Arial", 8)).pack()
        e = ttk.Entry(dlg, width=16)
        e.insert(0, str(default))
        e.pack()
        def apply():
            try:
                v = int(e.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Enter a valid integer.")
                return
            dlg.destroy()
            callback(v)
        tk.Button(dlg, text="Set", bg="#d97706", fg="white",
                  font=("Arial", 10, "bold"), command=apply).pack(pady=14)

    def camera_limit_dialog(self):
        username = self._selected_username()
        if not username:
            return
        u = self._get_user_data(username)
        self._simple_int_dialog(
            f"Camera Limit — {username}", "Max cameras:", u.get("max_cameras", 3),
            lambda v: self._send_sub(username, {"max_cameras": v}))

    def rule_limit_dialog(self):
        username = self._selected_username()
        if not username:
            return
        u = self._get_user_data(username)
        self._simple_int_dialog(
            f"Rule Limit — {username}", "Max blacklist/watch rules:", u.get("max_blacklist", 50),
            lambda v: self._send_sub(username, {"max_blacklist": v}), allow_minus_one=True)

    # ─── FEATURES DIALOG ─────────────────────────────────────────────────────

    def features_dialog(self):
        username = self._selected_username()
        if not username:
            return
        u = self._get_user_data(username)
        current = u.get("features", [])

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Feature Flags — {username}")
        dlg.geometry("480x320")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f"Feature flags for: {username}",
                 fg="#f8fafc", bg="#1e293b", font=("Arial", 11, "bold")).pack(pady=(14, 8))

        vars_ = {}
        for key, desc in ALL_FEATURES:
            v = tk.BooleanVar(value=(key in current))
            vars_[key] = v
            row = tk.Frame(dlg, bg="#1e293b")
            row.pack(fill=tk.X, padx=16, pady=2)
            tk.Checkbutton(row, text=key, variable=v, fg="#e2e8f0", bg="#1e293b",
                           selectcolor="#0f172a", activebackground="#1e293b",
                           font=("Courier", 9, "bold"), width=20, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=desc, fg="#94a3b8", bg="#1e293b",
                     font=("Arial", 8), anchor=tk.W).pack(side=tk.LEFT)

        btn_row = tk.Frame(dlg, bg="#1e293b")
        btn_row.pack(pady=12)
        for label, keys in [("Select All", [k for k, _ in ALL_FEATURES]),
                              ("Clear All", [])]:
            def toggle(kl=keys):
                for k, v in vars_.items():
                    v.set(k in kl)
            tk.Button(btn_row, text=label, bg="#475569", fg="white",
                      relief=tk.FLAT, command=toggle).pack(side=tk.LEFT, padx=6)

        def apply():
            features = [k for k, v in vars_.items() if v.get()]
            dlg.destroy()
            self._send_sub(username, {"features": features})
        tk.Button(dlg, text="Apply", bg="#0f766e", fg="white",
                  font=("Arial", 10, "bold"), command=apply).pack(pady=4)

    # ─── ADMIN NOTES ─────────────────────────────────────────────────────────

    def notes_dialog(self):
        username = self._selected_username()
        if not username:
            return
        u = self._get_user_data(username)
        current_notes = u.get("admin_notes", "") or ""

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Admin Notes — {username}")
        dlg.geometry("420x260")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text=f"Notes (internal, not visible to client):",
                 fg="#f8fafc", bg="#1e293b").pack(pady=(14, 4))
        txt = scrolledtext.ScrolledText(dlg, width=48, height=8, wrap=tk.WORD)
        txt.insert("1.0", current_notes)
        txt.pack(padx=12)
        def save():
            notes = txt.get("1.0", tk.END).strip()
            dlg.destroy()
            self._send_sub(username, {"admin_notes": notes, "days": 0})
        tk.Button(dlg, text="Save Notes", bg="#4338ca", fg="white",
                  font=("Arial", 10, "bold"), command=save).pack(pady=12)

    # ─── FULL CUSTOM DIALOG ──────────────────────────────────────────────────

    def full_custom_dialog(self, username):
        u = self._get_user_data(username)

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Custom Plan — {username}")
        dlg.geometry("540x600")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()

        # Header
        tk.Label(dlg, text=f"Custom Plan for: {username}",
                 fg="#f8fafc", bg="#1e293b", font=("Arial", 13, "bold")).pack(pady=(14, 4))
        tk.Label(dlg, text="Configure every aspect of this client's subscription",
                 fg="#94a3b8", bg="#1e293b", font=("Arial", 9)).pack(pady=(0, 10))

        # Scrollable frame
        canvas = tk.Canvas(dlg, bg="#1e293b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e293b")
        scroll_frame.bind("<Configure>",
                          lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=12)
        scrollbar.pack(side="right", fill="y")

        def section(text):
            f = tk.Frame(scroll_frame, bg="#0f172a", pady=4, padx=8)
            f.pack(fill=tk.X, pady=(8, 2))
            tk.Label(f, text=text, fg="#3b82f6", bg="#0f172a",
                     font=("Arial", 10, "bold")).pack(anchor=tk.W)
            return f

        def field_row(parent, label, widget_fn):
            row = tk.Frame(parent, bg="#1e293b")
            row.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(row, text=label, fg="#cbd5e1", bg="#1e293b", width=22,
                     anchor=tk.W).pack(side=tk.LEFT)
            w = widget_fn(row)
            w.pack(side=tk.LEFT, fill=tk.X, expand=True)
            return w

        # Section 1: Identity
        section("Subscription Plan")
        e_mode = field_row(scroll_frame, "Mode name:",
                           lambda p: ttk.Entry(p, width=22))
        e_mode.insert(0, u.get("subscription_mode", "custom") or "custom")

        # Section 2: Limits
        section("Limits")
        e_cams = field_row(scroll_frame, "Max cameras:",
                           lambda p: ttk.Entry(p, width=10))
        e_cams.insert(0, str(u.get("max_cameras", 3) or 3))

        e_rules = field_row(scroll_frame, "Max watch rules (−1=∞):",
                            lambda p: ttk.Entry(p, width=10))
        e_rules.insert(0, str(u.get("max_blacklist", 50) or 50))

        # Section 3: Subscription duration
        section("Subscription Duration")
        e_days = field_row(scroll_frame, "Days to add:",
                           lambda p: ttk.Entry(p, width=10))
        e_days.insert(0, "365")

        # Section 4: Feature flags
        section("Feature Flags")
        current_feats = u.get("features", [])
        feat_vars = {}
        for key, desc in ALL_FEATURES:
            v = tk.BooleanVar(value=(key in current_feats))
            feat_vars[key] = v
            row = tk.Frame(scroll_frame, bg="#1e293b")
            row.pack(fill=tk.X, padx=16, pady=1)
            tk.Checkbutton(row, text=key, variable=v, fg="#e2e8f0", bg="#1e293b",
                           selectcolor="#0f172a", activebackground="#1e293b",
                           font=("Courier", 9, "bold"), width=22, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=desc, fg="#94a3b8", bg="#1e293b",
                     font=("Arial", 8), anchor=tk.W).pack(side=tk.LEFT)

        # Quick preset buttons in feature section
        preset_row = tk.Frame(scroll_frame, bg="#1e293b")
        preset_row.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(preset_row, text="Preset:", fg="#94a3b8", bg="#1e293b",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 4))
        for pkey, pinfo in SUBSCRIPTION_MODES.items():
            if pkey == "custom":
                continue
            def set_feats(keys=pinfo["features"]):
                for k, v in feat_vars.items():
                    v.set(k in keys)
            tk.Button(preset_row, text=pinfo["label"], bg=pinfo["color"], fg="white",
                      relief=tk.FLAT, font=("Arial", 8),
                      command=set_feats).pack(side=tk.LEFT, padx=2, ipadx=4, ipady=1)

        # Section 5: Admin notes
        section("Internal Notes")
        notes_frame = tk.Frame(scroll_frame, bg="#1e293b")
        notes_frame.pack(fill=tk.X, padx=16, pady=4)
        txt_notes = scrolledtext.ScrolledText(notes_frame, width=52, height=4, wrap=tk.WORD,
                                               bg="#0f172a", fg="#e2e8f0")
        txt_notes.insert("1.0", u.get("admin_notes", "") or "")
        txt_notes.pack()

        # Apply button
        def apply():
            try:
                cams = int(e_cams.get().strip())
                rules = int(e_rules.get().strip())
                days = int(e_days.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Cameras, Rules and Days must be integers.")
                return
            features = [k for k, v in feat_vars.items() if v.get()]
            payload = {
                "mode": e_mode.get().strip() or "custom",
                "max_cameras": cams,
                "max_blacklist": rules,
                "days": days,
                "features": features,
                "admin_notes": txt_notes.get("1.0", tk.END).strip(),
            }
            dlg.destroy()
            self._send_sub(username, payload)

        apply_btn = tk.Button(dlg, text="Apply Custom Plan", bg="#2563eb", fg="white",
                              font=("Arial", 11, "bold"), relief=tk.FLAT, command=apply)
        apply_btn.pack(side=tk.BOTTOM, pady=10, ipadx=16, ipady=4)

    # ─── VIEW CLIENT DETAILS ──────────────────────────────────────────────────

    def view_client_details(self):
        username = self._selected_username()
        if not username:
            return
        u = self._get_user_data(username)
        if not u:
            messagebox.showinfo("Details", "Refresh the list first.")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Client Details — {username}")
        dlg.geometry("420x360")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)

        tk.Label(dlg, text=username, fg="#f8fafc", bg="#1e293b",
                 font=("Arial", 14, "bold")).pack(pady=(14, 2))
        mode = (u.get("subscription_mode") or "standard").capitalize()
        tk.Label(dlg, text=f"Mode: {mode}", fg="#3b82f6", bg="#1e293b",
                 font=("Arial", 10, "bold")).pack()

        info = tk.Frame(dlg, bg="#0f172a", padx=16, pady=10)
        info.pack(fill=tk.X, padx=16, pady=8)
        fields = [
            ("Created", u.get("created_at", "—")),
            ("Valid Until", u.get("subscription_end", "Unlimited")),
            ("Max Cameras", str(u.get("max_cameras", 3))),
            ("Max Rules", "∞" if u.get("max_blacklist") == -1 else str(u.get("max_blacklist", 50))),
        ]
        for label, val in fields:
            row = tk.Frame(info, bg="#0f172a")
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=f"{label}:", fg="#94a3b8", bg="#0f172a",
                     width=14, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=val, fg="#e2e8f0", bg="#0f172a",
                     font=("Arial", 9, "bold")).pack(side=tk.LEFT)

        feats = u.get("features", [])
        tk.Label(dlg, text="Features:", fg="#94a3b8", bg="#1e293b",
                 font=("Arial", 9)).pack(anchor=tk.W, padx=16)
        feat_txt = scrolledtext.ScrolledText(dlg, width=48, height=4, wrap=tk.WORD,
                                              bg="#0f172a", fg="#22c55e", state=tk.NORMAL)
        feat_txt.insert("1.0", "\n".join(feats) if feats else "(none)")
        feat_txt.config(state=tk.DISABLED)
        feat_txt.pack(padx=16)

        notes = u.get("admin_notes", "") or ""
        if notes:
            tk.Label(dlg, text="Notes:", fg="#94a3b8", bg="#1e293b",
                     font=("Arial", 9)).pack(anchor=tk.W, padx=16, pady=(6, 0))
            n_txt = scrolledtext.ScrolledText(dlg, width=48, height=3, wrap=tk.WORD,
                                               bg="#0f172a", fg="#fbbf24", state=tk.NORMAL)
            n_txt.insert("1.0", notes)
            n_txt.config(state=tk.DISABLED)
            n_txt.pack(padx=16)


if __name__ == "__main__":
    root = tk.Tk()
    app = AdminPCSApp(root)
    root.mainloop()
