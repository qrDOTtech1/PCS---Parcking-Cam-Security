"""
PCS Admin Control Panel — Multi-Server Edition
Gère les comptes clients sur PCS et PCS-AI (serveurs séparés).
Config persistée dans admin_config.json (côté à admin_tools.py).
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import requests
import threading
import time
import json
import os
from pathlib import Path

# ── Config file ─────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "admin_config.json"

DEFAULT_CONFIG = {
    "servers": [
        {
            "id": "pcs",
            "name": "PCS (Main)",
            "base_url": "https://web-production-10852.up.railway.app",
            "master_key": "master_key_pcs_2024",
            "color": "#3b82f6",
        },
        {
            "id": "pcs_ai",
            "name": "PCS-AI",
            "base_url": "https://your-pcs-ai.up.railway.app",
            "master_key": "master_key_pcs_2024",
            "color": "#8b5cf6",
        },
    ]
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Subscription presets ─────────────────────────────────────────────────────

ALL_FEATURES = [
    ("vehicle_filters", "Vehicle type/color filters"),
    ("type_color_only", "Type+Color rules without plate"),
    ("priority_alerts", "Custom labels & priority levels"),
    ("anpr_history", "Full ANPR detection history"),
    ("multi_notify", "Multiple notification targets"),
    ("gps_tracking", "GPS tracking per camera"),
    ("unlimited_blacklist", "Unlimited watch rules"),
    ("ai_summaries", "PCS-AI periodic summaries access"),
]

MODES = {
    "standard": {
        "label": "Standard",
        "color": "#3b82f6",
        "max_cameras": 3,
        "max_blacklist": 20,
        "days": 30,
        "features": [],
    },
    "pro": {
        "label": "Pro",
        "color": "#8b5cf6",
        "max_cameras": 10,
        "max_blacklist": 100,
        "days": 365,
        "features": [
            "vehicle_filters",
            "priority_alerts",
            "anpr_history",
            "multi_notify",
            "gps_tracking",
        ],
    },
    "emergency": {
        "label": "Centre d'Urgence",
        "color": "#ef4444",
        "max_cameras": 50,
        "max_blacklist": 500,
        "days": 365,
        "features": [
            "vehicle_filters",
            "type_color_only",
            "priority_alerts",
            "anpr_history",
            "multi_notify",
            "gps_tracking",
        ],
    },
    "enterprise": {
        "label": "Enterprise",
        "color": "#f59e0b",
        "max_cameras": 100,
        "max_blacklist": -1,
        "days": 365,
        "features": [f[0] for f in ALL_FEATURES],
    },
    "custom": {
        "label": "Custom",
        "color": "#6b7280",
        "max_cameras": None,
        "max_blacklist": None,
        "days": None,
        "features": [],
    },
}

MAX_RETRIES = 2
RETRY_DELAY = 1.5

# ── HTTP helper ──────────────────────────────────────────────────────────────


def _req(method, url, master_key, **kwargs):
    kwargs.setdefault("timeout", (8, 30))
    kwargs.setdefault("headers", {})["X-Master-Key"] = master_key
    for attempt in range(MAX_RETRIES):
        try:
            return requests.request(method, url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ── Main App ─────────────────────────────────────────────────────────────────


class AdminPCSApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCS Admin — Multi-Server")
        self.root.geometry("1000x720")
        self.root.configure(bg="#0f172a")
        self.config = load_config()
        self._users_cache = {}  # server_id -> list[user_dict]
        self._build_ui()
        self._auto_refresh()

    # ── Build UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#0f172a", borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#1e293b",
            foreground="#94a3b8",
            padding=[12, 6],
            font=("Arial", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "white")],
        )

        # ── Header ──
        hdr = tk.Frame(self.root, bg="#1e293b", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr,
            text="NovaSecurity — Admin Panel",
            font=("Arial", 15, "bold"),
            fg="#3b82f6",
            bg="#1e293b",
        ).pack(side=tk.LEFT, padx=16)
        self.global_status = tk.Label(
            hdr, text="", font=("Arial", 9), fg="#fbbf24", bg="#1e293b"
        )
        self.global_status.pack(side=tk.LEFT, padx=8)
        tk.Button(
            hdr,
            text="Manage Servers",
            bg="#7c3aed",
            fg="white",
            relief=tk.FLAT,
            command=self._servers_dialog,
        ).pack(side=tk.RIGHT, padx=6)
        tk.Button(
            hdr,
            text="Refresh All",
            bg="#059669",
            fg="white",
            relief=tk.FLAT,
            command=self._refresh_all,
        ).pack(side=tk.RIGHT, padx=4)

        # ── Create account strip (top, cross-server) ──
        create_strip = tk.Frame(self.root, bg="#1e293b", pady=8, padx=12)
        create_strip.pack(fill=tk.X)
        tk.Label(
            create_strip,
            text="New Client:",
            font=("Arial", 10, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(create_strip, text="Username", fg="#94a3b8", bg="#1e293b").pack(
            side=tk.LEFT
        )
        self.e_user = ttk.Entry(create_strip, width=18)
        self.e_user.pack(side=tk.LEFT, padx=4)
        tk.Label(create_strip, text="Password", fg="#94a3b8", bg="#1e293b").pack(
            side=tk.LEFT
        )
        self.e_pass = ttk.Entry(create_strip, width=18, show="*")
        self.e_pass.pack(side=tk.LEFT, padx=4)

        # Checkbox per server
        self._create_on = {}
        for srv in self.config["servers"]:
            var = tk.BooleanVar(value=True)
            self._create_on[srv["id"]] = var
            tk.Checkbutton(
                create_strip,
                text=srv["name"],
                variable=var,
                fg=srv.get("color", "#3b82f6"),
                bg="#1e293b",
                selectcolor="#0f172a",
                activebackground="#1e293b",
                font=("Arial", 9, "bold"),
            ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            create_strip,
            text="Create Account(s)",
            bg="#2563eb",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=self._create_user,
        ).pack(side=tk.LEFT, padx=10)

        # ── Notebook — one tab per server ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._server_tabs = {}
        for srv in self.config["servers"]:
            self._add_server_tab(srv)

    def _add_server_tab(self, srv):
        frame = tk.Frame(self.notebook, bg="#0f172a")
        self.notebook.add(frame, text=srv["name"])
        self._server_tabs[srv["id"]] = {"frame": frame, "srv": srv}
        self._build_server_tab(srv["id"])

    def _build_server_tab(self, srv_id):
        info = self._server_tabs[srv_id]
        frame = info["frame"]
        srv = info["srv"]

        # Clear
        for w in frame.winfo_children():
            w.destroy()

        # Status bar
        sbar = tk.Frame(frame, bg="#1e293b", pady=4, padx=8)
        sbar.pack(fill=tk.X)
        status_lbl = tk.Label(
            sbar,
            text=f"URL: {srv['base_url']}",
            fg="#64748b",
            bg="#1e293b",
            font=("Arial", 8),
        )
        status_lbl.pack(side=tk.LEFT)
        conn_lbl = tk.Label(
            sbar, text="●", fg="#fbbf24", bg="#1e293b", font=("Arial", 12)
        )
        conn_lbl.pack(side=tk.LEFT, padx=6)
        info["conn_lbl"] = conn_lbl
        info["status_lbl"] = status_lbl

        tk.Button(
            sbar,
            text="Test",
            bg="#475569",
            fg="white",
            relief=tk.FLAT,
            command=lambda s=srv_id: self._test_connection(s),
        ).pack(side=tk.RIGHT, padx=4)
        tk.Button(
            sbar,
            text="Refresh",
            bg="#059669",
            fg="white",
            relief=tk.FLAT,
            command=lambda s=srv_id: self._load_users(s),
        ).pack(side=tk.RIGHT, padx=4)

        # Treeview
        cols = (
            "id",
            "username",
            "mode",
            "cameras",
            "rules",
            "features",
            "valid_until",
            "notes",
        )
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=10)
        specs = [
            ("id", "ID", 32),
            ("username", "Username", 115),
            ("mode", "Mode", 90),
            ("cameras", "Cam", 40),
            ("rules", "Rules", 45),
            ("features", "Features", 190),
            ("valid_until", "Valid Until", 90),
            ("notes", "Notes", 100),
        ]
        for col, heading, width in specs:
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor=tk.CENTER if width < 80 else tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 2))
        info["tree"] = tree

        # Action row 1 — days
        r1 = tk.Frame(frame, bg="#0f172a")
        r1.pack(fill=tk.X, padx=8, pady=(2, 1))
        tk.Label(
            r1, text="+ Days:", fg="#94a3b8", bg="#0f172a", font=("Arial", 8)
        ).pack(side=tk.LEFT, padx=(0, 3))
        for days, label, color in [
            (30, "+30d", "#16a34a"),
            (90, "+90d", "#15803d"),
            (365, "+1yr", "#14532d"),
        ]:
            tk.Button(
                r1,
                text=label,
                bg=color,
                fg="white",
                font=("Arial", 8, "bold"),
                relief=tk.FLAT,
                command=lambda d=days, s=srv_id: self._add_sub(s, d),
            ).pack(side=tk.LEFT, padx=2, ipadx=5, ipady=1)
        tk.Button(
            r1,
            text="Custom…",
            bg="#475569",
            fg="white",
            font=("Arial", 8),
            relief=tk.FLAT,
            command=lambda s=srv_id: self._custom_days_dlg(s),
        ).pack(side=tk.LEFT, padx=4, ipadx=5, ipady=1)

        # Action row 2 — modes
        r2 = tk.Frame(frame, bg="#0f172a")
        r2.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(r2, text="Mode:", fg="#94a3b8", bg="#0f172a", font=("Arial", 8)).pack(
            side=tk.LEFT, padx=(0, 3)
        )
        for key, mi in MODES.items():
            tk.Button(
                r2,
                text=mi["label"],
                bg=mi["color"],
                fg="white",
                font=("Arial", 8, "bold"),
                relief=tk.FLAT,
                command=lambda k=key, s=srv_id: self._apply_mode(s, k),
            ).pack(side=tk.LEFT, padx=2, ipadx=4, ipady=1)

        # Action row 3 — other
        r3 = tk.Frame(frame, bg="#0f172a")
        r3.pack(fill=tk.X, padx=8, pady=(1, 6))
        actions = [
            ("Camera Limit…", "#d97706", lambda s=srv_id: self._cam_limit_dlg(s)),
            ("Rule Limit…", "#b45309", lambda s=srv_id: self._rule_limit_dlg(s)),
            ("Features…", "#0f766e", lambda s=srv_id: self._features_dlg(s)),
            ("Notes…", "#4338ca", lambda s=srv_id: self._notes_dlg(s)),
            ("Details", "#0ea5e9", lambda s=srv_id: self._details_dlg(s)),
        ]
        for label, color, cmd in actions:
            tk.Button(
                r3,
                text=label,
                bg=color,
                fg="white",
                font=("Arial", 8, "bold"),
                relief=tk.FLAT,
                command=cmd,
            ).pack(side=tk.LEFT, padx=2, ipadx=5, ipady=1)
        tk.Button(
            r3,
            text="Delete Client",
            bg="#dc2626",
            fg="white",
            font=("Arial", 8, "bold"),
            relief=tk.FLAT,
            command=lambda s=srv_id: self._delete_user(s),
        ).pack(side=tk.RIGHT, padx=2, ipadx=6, ipady=1)
        # Cross-server copy
        other_servers = [s for s in self.config["servers"] if s["id"] != srv_id]
        if other_servers:
            tk.Button(
                r3,
                text="Copy to →",
                bg="#1e3a5f",
                fg="white",
                font=("Arial", 8, "bold"),
                relief=tk.FLAT,
                command=lambda s=srv_id: self._copy_user_dlg(s),
            ).pack(side=tk.RIGHT, padx=2, ipadx=5, ipady=1)

    # ── Load users ────────────────────────────────────────────────────────────

    def _load_users(self, srv_id, silent=False):
        info = self._server_tabs.get(srv_id)
        if not info:
            return
        srv = info["srv"]
        try:
            res = _req("GET", f"{srv['base_url']}/api/admin/users", srv["master_key"])
            if res.status_code == 200:
                data = res.json()
                users = data.get("users", [])
                self._users_cache[srv_id] = users
                tree = info["tree"]
                for row in tree.get_children():
                    tree.delete(row)
                for u in users:
                    feats = u.get("features", [])
                    rules = u.get("max_blacklist", 50)
                    notes = u.get("admin_notes", "") or ""
                    tree.insert(
                        "",
                        tk.END,
                        values=(
                            u["id"],
                            u["username"],
                            (u.get("subscription_mode") or "standard").capitalize(),
                            u.get("max_cameras", 3),
                            "∞" if rules == -1 else str(rules),
                            ", ".join(feats) if feats else "—",
                            u.get("subscription_end", "Unlimited"),
                            notes[:22] + "…" if len(notes) > 22 else notes,
                        ),
                    )
                info["conn_lbl"].config(text="●", fg="#22c55e")
                if not silent:
                    self._set_global(f"{srv['name']}: {len(users)} clients", "#22c55e")
            elif res.status_code == 401:
                info["conn_lbl"].config(text="●", fg="#ef4444")
                if not silent:
                    messagebox.showerror(
                        "Auth Error",
                        f"{srv['name']}: Invalid Master Key.\nCheck server settings.",
                    )
            else:
                info["conn_lbl"].config(text="●", fg="#f59e0b")
        except Exception as e:
            info["conn_lbl"].config(text="●", fg="#ef4444")
            if not silent:
                messagebox.showerror("Connection Error", f"{srv['name']}:\n{e}")

    def _refresh_all(self):
        self._set_global("Refreshing all servers…", "#fbbf24")
        for srv_id in self._server_tabs:
            threading.Thread(
                target=self._load_users, args=(srv_id, True), daemon=True
            ).start()

    def _auto_refresh(self):
        self._refresh_all()

    def _test_connection(self, srv_id):
        info = self._server_tabs[srv_id]
        srv = info["srv"]
        try:
            res = _req("GET", f"{srv['base_url']}/api/admin/users", srv["master_key"])
            if res.status_code in [200, 401]:
                messagebox.showinfo(
                    "OK", f"{srv['name']} responding — HTTP {res.status_code}"
                )
            else:
                messagebox.showwarning("Status", f"HTTP {res.status_code}")
        except Exception as e:
            messagebox.showerror("Failed", str(e))

    def _set_global(self, msg, color="#fbbf24"):
        self.global_status.config(text=msg, fg=color)

    # ── Selected user helpers ─────────────────────────────────────────────────

    def _selected(self, srv_id):
        tree = self._server_tabs[srv_id]["tree"]
        sel = tree.focus()
        if not sel:
            messagebox.showwarning("Selection", "Select a client first.")
            return None, None
        username = tree.item(sel, "values")[1]
        user_data = next(
            (u for u in self._users_cache.get(srv_id, []) if u["username"] == username),
            {},
        )
        return username, user_data

    def _send_sub(self, srv_id, username, payload):
        srv = self._server_tabs[srv_id]["srv"]
        self._set_global("Updating…", "#fbbf24")
        try:
            res = _req(
                "POST",
                f"{srv['base_url']}/api/admin/users/{username}/subscription",
                srv["master_key"],
                json=payload,
            )
            data = res.json()
            if res.status_code == 200:
                self._set_global(data.get("message", "Updated"), "#22c55e")
                self._load_users(srv_id, silent=True)
            else:
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            messagebox.showerror("Network Error", str(e))

    # ── Create user ───────────────────────────────────────────────────────────

    def _create_user(self):
        user = self.e_user.get().strip()
        pwd = self.e_pass.get().strip()
        if not user or not pwd:
            messagebox.showwarning("Validation", "Username and password required.")
            return

        targets = [
            srv
            for srv in self.config["servers"]
            if self._create_on.get(srv["id"], tk.BooleanVar()).get()
        ]
        if not targets:
            messagebox.showwarning("Targets", "Select at least one server.")
            return

        results = []
        for srv in targets:
            try:
                res = _req(
                    "POST",
                    f"{srv['base_url']}/api/admin/users",
                    srv["master_key"],
                    json={"username": user, "password": pwd},
                )
                data = res.json()
                status = (
                    "✓" if res.status_code == 201 else f"✗ {data.get('message', 'err')}"
                )
                results.append(f"{srv['name']}: {status}")
                if res.status_code == 201:
                    self._load_users(srv["id"], silent=True)
            except Exception as e:
                results.append(f"{srv['name']}: ✗ {e}")

        messagebox.showinfo("Create Results", "\n".join(results))
        self.e_user.delete(0, tk.END)
        self.e_pass.delete(0, tk.END)

    # ── Delete user ───────────────────────────────────────────────────────────

    def _delete_user(self, srv_id):
        username, _ = self._selected(srv_id)
        if not username:
            return
        srv = self._server_tabs[srv_id]["srv"]
        if not messagebox.askyesno(
            "Confirm", f"Delete '{username}' from {srv['name']}?"
        ):
            return
        try:
            res = _req(
                "DELETE",
                f"{srv['base_url']}/api/admin/users/{username}",
                srv["master_key"],
            )
            data = res.json()
            if res.status_code == 200:
                self._set_global("Deleted!", "#22c55e")
                self._load_users(srv_id, silent=True)
            else:
                messagebox.showerror("Error", data.get("message", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── Subscription ──────────────────────────────────────────────────────────

    def _add_sub(self, srv_id, days):
        username, _ = self._selected(srv_id)
        if username:
            self._send_sub(srv_id, username, {"days": days})

    def _custom_days_dlg(self, srv_id):
        username, _ = self._selected(srv_id)
        if not username:
            return
        dlg = self._mini_dialog(f"Custom Days — {username}", "Days to add:", "180")

        def apply(val):
            try:
                self._send_sub(srv_id, username, {"days": int(val)})
            except ValueError:
                messagebox.showerror("Error", "Enter a valid integer.")

        dlg[1].config(command=lambda: [apply(dlg[0].get()), dlg[2].destroy()])

    def _apply_mode(self, srv_id, mode_key):
        username, _ = self._selected(srv_id)
        if not username:
            return
        if mode_key == "custom":
            self._full_custom_dlg(srv_id, username)
            return
        mi = MODES[mode_key]
        msg = (
            f"Apply '{mi['label']}' to '{username}' on {self._server_tabs[srv_id]['srv']['name']}?\n\n"
            f"  Cameras : {mi['max_cameras']}\n"
            f"  Rules   : {'∞' if mi['max_blacklist'] == -1 else mi['max_blacklist']}\n"
            f"  Days    : +{mi['days']}\n"
            f"  Features: {len(mi['features'])}"
        )
        if not messagebox.askyesno("Confirm", msg):
            return
        self._send_sub(
            srv_id,
            username,
            {
                "mode": mode_key,
                "max_cameras": mi["max_cameras"],
                "max_blacklist": mi["max_blacklist"],
                "days": mi["days"],
                "features": mi["features"],
            },
        )

    # ── Quick limit dialogs ───────────────────────────────────────────────────

    def _cam_limit_dlg(self, srv_id):
        username, u = self._selected(srv_id)
        if not username:
            return
        dlg = self._mini_dialog(
            f"Camera Limit — {username}",
            "Max cameras:",
            str(u.get("max_cameras", 3)),
            allow_neg=False,
        )

        def apply(val):
            try:
                self._send_sub(srv_id, username, {"max_cameras": int(val)})
            except ValueError:
                messagebox.showerror("Error", "Enter a valid integer.")

        dlg[1].config(command=lambda: [apply(dlg[0].get()), dlg[2].destroy()])

    def _rule_limit_dlg(self, srv_id):
        username, u = self._selected(srv_id)
        if not username:
            return
        dlg = self._mini_dialog(
            f"Rule Limit — {username}",
            "Max rules (−1=∞):",
            str(u.get("max_blacklist", 50)),
            allow_neg=True,
        )

        def apply(val):
            try:
                self._send_sub(srv_id, username, {"max_blacklist": int(val)})
            except ValueError:
                messagebox.showerror("Error", "Enter a valid integer.")

        dlg[1].config(command=lambda: [apply(dlg[0].get()), dlg[2].destroy()])

    def _mini_dialog(self, title, label, default, allow_neg=False):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("290x140")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text=label, fg="#f8fafc", bg="#1e293b").pack(pady=(16, 3))
        if allow_neg:
            tk.Label(
                dlg,
                text="(−1 = unlimited)",
                fg="#94a3b8",
                bg="#1e293b",
                font=("Arial", 8),
            ).pack()
        entry = ttk.Entry(dlg, width=14)
        entry.insert(0, default)
        entry.pack(pady=4)
        btn = tk.Button(
            dlg, text="Set", bg="#2563eb", fg="white", font=("Arial", 10, "bold")
        )
        btn.pack(pady=8)
        return entry, btn, dlg

    # ── Features dialog ───────────────────────────────────────────────────────

    def _features_dlg(self, srv_id):
        username, u = self._selected(srv_id)
        if not username:
            return
        current = u.get("features", [])
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Features — {username}")
        dlg.geometry("500x310")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(
            dlg,
            text=f"Feature flags — {username}",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Arial", 11, "bold"),
        ).pack(pady=(12, 6))
        vars_ = {}
        for key, desc in ALL_FEATURES:
            v = tk.BooleanVar(value=(key in current))
            vars_[key] = v
            row = tk.Frame(dlg, bg="#1e293b")
            row.pack(fill=tk.X, padx=16, pady=1)
            tk.Checkbutton(
                row,
                text=key,
                variable=v,
                fg="#e2e8f0",
                bg="#1e293b",
                selectcolor="#0f172a",
                activebackground="#1e293b",
                font=("Courier", 9, "bold"),
                width=22,
                anchor=tk.W,
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=desc, fg="#94a3b8", bg="#1e293b", font=("Arial", 8)
            ).pack(side=tk.LEFT)
        pr = tk.Frame(dlg, bg="#1e293b")
        pr.pack(pady=6)
        for label, keys in [("All", [k for k, _ in ALL_FEATURES]), ("None", [])]:

            def toggle(kl=keys):
                for k, v in vars_.items():
                    v.set(k in kl)

            tk.Button(
                pr, text=label, bg="#475569", fg="white", relief=tk.FLAT, command=toggle
            ).pack(side=tk.LEFT, padx=4)

        def apply():
            feats = [k for k, v in vars_.items() if v.get()]
            dlg.destroy()
            self._send_sub(srv_id, username, {"features": feats})

        tk.Button(
            dlg,
            text="Apply",
            bg="#0f766e",
            fg="white",
            font=("Arial", 10, "bold"),
            command=apply,
        ).pack(pady=4)

    # ── Notes dialog ──────────────────────────────────────────────────────────

    def _notes_dlg(self, srv_id):
        username, u = self._selected(srv_id)
        if not username:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Notes — {username}")
        dlg.geometry("420,240")
        dlg.geometry("420x240")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(
            dlg,
            text="Internal notes (not visible to client):",
            fg="#f8fafc",
            bg="#1e293b",
        ).pack(pady=(12, 4))
        txt = scrolledtext.ScrolledText(dlg, width=50, height=7, wrap=tk.WORD)
        txt.insert("1.0", u.get("admin_notes", "") or "")
        txt.pack(padx=12)

        def save():
            notes = txt.get("1.0", tk.END).strip()
            dlg.destroy()
            self._send_sub(srv_id, username, {"admin_notes": notes, "days": 0})

        tk.Button(
            dlg,
            text="Save",
            bg="#4338ca",
            fg="white",
            font=("Arial", 10, "bold"),
            command=save,
        ).pack(pady=8)

    # ── Details dialog ────────────────────────────────────────────────────────

    def _details_dlg(self, srv_id):
        username, u = self._selected(srv_id)
        if not username:
            return
        srv = self._server_tabs[srv_id]["srv"]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Details — {username} @ {srv['name']}")
        dlg.geometry("440x380")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        tk.Label(
            dlg, text=username, fg="#f8fafc", bg="#1e293b", font=("Arial", 14, "bold")
        ).pack(pady=(12, 2))
        tk.Label(
            dlg,
            text=f"Server: {srv['name']}  |  Mode: {(u.get('subscription_mode') or 'standard').capitalize()}",
            fg="#3b82f6",
            bg="#1e293b",
            font=("Arial", 9, "bold"),
        ).pack()
        info_frame = tk.Frame(dlg, bg="#0f172a", padx=16, pady=8)
        info_frame.pack(fill=tk.X, padx=16, pady=6)
        fields = [
            ("Created", u.get("created_at", "—")),
            ("Valid Until", u.get("subscription_end", "Unlimited")),
            ("Max Cameras", str(u.get("max_cameras", 3))),
            (
                "Max Rules",
                "∞"
                if u.get("max_blacklist") == -1
                else str(u.get("max_blacklist", 50)),
            ),
        ]
        for lbl, val in fields:
            row = tk.Frame(info_frame, bg="#0f172a")
            row.pack(fill=tk.X, pady=1)
            tk.Label(
                row, text=f"{lbl}:", fg="#94a3b8", bg="#0f172a", width=14, anchor=tk.W
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=val, fg="#e2e8f0", bg="#0f172a", font=("Arial", 9, "bold")
            ).pack(side=tk.LEFT)
        feats = u.get("features", [])
        tk.Label(dlg, text="Features:", fg="#94a3b8", bg="#1e293b").pack(
            anchor=tk.W, padx=16
        )
        ft = scrolledtext.ScrolledText(
            dlg, width=50, height=4, bg="#0f172a", fg="#22c55e", state=tk.NORMAL
        )
        ft.insert("1.0", "\n".join(feats) if feats else "(none)")
        ft.config(state=tk.DISABLED)
        ft.pack(padx=16)
        notes = u.get("admin_notes", "") or ""
        if notes:
            tk.Label(dlg, text="Notes:", fg="#94a3b8", bg="#1e293b").pack(
                anchor=tk.W, padx=16
            )
            nt = scrolledtext.ScrolledText(
                dlg, width=50, height=3, bg="#0f172a", fg="#fbbf24", state=tk.NORMAL
            )
            nt.insert("1.0", notes)
            nt.config(state=tk.DISABLED)
            nt.pack(padx=16)

    # ── Copy user to another server ───────────────────────────────────────────

    def _copy_user_dlg(self, src_srv_id):
        username, u = self._selected(src_srv_id)
        if not username:
            return
        other = [s for s in self.config["servers"] if s["id"] != src_srv_id]
        if not other:
            messagebox.showinfo("No targets", "No other servers configured.")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Copy '{username}' to other server")
        dlg.geometry("380x260")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=f"Copy account '{username}' to:",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Arial", 11, "bold"),
        ).pack(pady=(14, 8))

        target_var = tk.StringVar(value=other[0]["id"])
        for srv in other:
            tk.Radiobutton(
                dlg,
                text=srv["name"],
                variable=target_var,
                value=srv["id"],
                fg=srv.get("color", "white"),
                bg="#1e293b",
                selectcolor="#0f172a",
                activebackground="#1e293b",
                font=("Arial", 10, "bold"),
            ).pack(padx=20, anchor=tk.W)

        tk.Label(
            dlg,
            text="New password (leave blank = keep same):",
            fg="#94a3b8",
            bg="#1e293b",
            font=("Arial", 9),
        ).pack(pady=(10, 3))
        e_pwd = ttk.Entry(dlg, width=28, show="*")
        e_pwd.pack()

        tk.Label(
            dlg,
            text="Also copy subscription/features/mode?",
            fg="#94a3b8",
            bg="#1e293b",
            font=("Arial", 9),
        ).pack(pady=(6, 2))
        copy_sub_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            dlg,
            text="Yes, copy full plan",
            variable=copy_sub_var,
            fg="#e2e8f0",
            bg="#1e293b",
            selectcolor="#0f172a",
            activebackground="#1e293b",
        ).pack()

        def apply():
            dst_id = target_var.get()
            dst_srv = next(s for s in self.config["servers"] if s["id"] == dst_id)
            pwd = e_pwd.get().strip()
            if not pwd:
                messagebox.showwarning(
                    "Password required",
                    "A new password is required to create the account on the target server.\n"
                    "The original password hash cannot be transferred.",
                )
                return

            results = []
            # 1. Create account on target
            try:
                res = _req(
                    "POST",
                    f"{dst_srv['base_url']}/api/admin/users",
                    dst_srv["master_key"],
                    json={"username": username, "password": pwd},
                )
                data = res.json()
                if res.status_code == 201:
                    results.append(f"✓ Account created on {dst_srv['name']}")
                    # 2. Copy subscription if requested
                    if copy_sub_var.get():
                        payload = {
                            "mode": u.get("subscription_mode", "standard"),
                            "max_cameras": u.get("max_cameras", 3),
                            "max_blacklist": u.get("max_blacklist", 50),
                            "features": u.get("features", []),
                            "admin_notes": u.get("admin_notes", ""),
                            "days": 0,
                        }
                        res2 = _req(
                            "POST",
                            f"{dst_srv['base_url']}/api/admin/users/{username}/subscription",
                            dst_srv["master_key"],
                            json=payload,
                        )
                        if res2.status_code == 200:
                            results.append("✓ Subscription/features copied")
                        else:
                            results.append(f"⚠ Sub copy failed: {res2.status_code}")
                    self._load_users(dst_id, silent=True)
                else:
                    results.append(f"✗ {data.get('message', 'Unknown error')}")
            except Exception as e:
                results.append(f"✗ {e}")

            dlg.destroy()
            messagebox.showinfo("Copy Results", "\n".join(results))

        tk.Button(
            dlg,
            text="Copy Account",
            bg="#1e3a5f",
            fg="white",
            font=("Arial", 10, "bold"),
            command=apply,
        ).pack(pady=12)

    # ── Full custom dialog ────────────────────────────────────────────────────

    def _full_custom_dlg(self, srv_id, username):
        u = next(
            (x for x in self._users_cache.get(srv_id, []) if x["username"] == username),
            {},
        )
        srv = self._server_tabs[srv_id]["srv"]

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Custom Plan — {username} @ {srv['name']}")
        dlg.geometry("560x620")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=f"Custom Plan — {username}",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Arial", 13, "bold"),
        ).pack(pady=(12, 2))
        tk.Label(
            dlg,
            text=f"Server: {srv['name']}",
            fg=srv.get("color", "#3b82f6"),
            bg="#1e293b",
            font=("Arial", 9, "bold"),
        ).pack()

        canvas = tk.Canvas(dlg, bg="#1e293b", highlightthickness=0)
        sb = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        sf = tk.Frame(canvas, bg="#1e293b")
        sf.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=12)
        sb.pack(side="right", fill="y")

        def section(txt):
            f = tk.Frame(sf, bg="#0f172a", pady=3, padx=8)
            f.pack(fill=tk.X, pady=(6, 1))
            tk.Label(
                f, text=txt, fg="#3b82f6", bg="#0f172a", font=("Arial", 10, "bold")
            ).pack(anchor=tk.W)

        def field(lbl, default):
            row = tk.Frame(sf, bg="#1e293b")
            row.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(
                row, text=lbl, fg="#cbd5e1", bg="#1e293b", width=24, anchor=tk.W
            ).pack(side=tk.LEFT)
            e = ttk.Entry(row, width=18)
            e.insert(0, str(default))
            e.pack(side=tk.LEFT)
            return e

        section("Plan Identity")
        e_mode = field("Mode name:", u.get("subscription_mode", "custom") or "custom")

        section("Limits")
        e_cams = field("Max cameras:", u.get("max_cameras", 3) or 3)
        e_rules = field("Max rules (−1=∞):", u.get("max_blacklist", 50) or 50)
        tk.Label(
            sf,
            text="  −1 = unlimited rules",
            fg="#475569",
            bg="#1e293b",
            font=("Arial", 8),
        ).pack(anchor=tk.W, padx=32)

        section("Subscription Duration")
        e_days = field("Days to add:", 365)

        section("Feature Flags")
        current = u.get("features", [])
        fvars = {}
        for key, desc in ALL_FEATURES:
            v = tk.BooleanVar(value=(key in current))
            fvars[key] = v
            row = tk.Frame(sf, bg="#1e293b")
            row.pack(fill=tk.X, padx=16, pady=1)
            tk.Checkbutton(
                row,
                text=key,
                variable=v,
                fg="#e2e8f0",
                bg="#1e293b",
                selectcolor="#0f172a",
                activebackground="#1e293b",
                font=("Courier", 9, "bold"),
                width=24,
                anchor=tk.W,
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=desc, fg="#94a3b8", bg="#1e293b", font=("Arial", 8)
            ).pack(side=tk.LEFT)

        pr = tk.Frame(sf, bg="#1e293b")
        pr.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(
            pr, text="Preset:", fg="#94a3b8", bg="#1e293b", font=("Arial", 8)
        ).pack(side=tk.LEFT, padx=(0, 4))
        for pk, pi in MODES.items():
            if pk == "custom":
                continue

            def sfn(kl=pi["features"]):
                for k, v in fvars.items():
                    v.set(k in kl)

            tk.Button(
                pr,
                text=pi["label"],
                bg=pi["color"],
                fg="white",
                relief=tk.FLAT,
                font=("Arial", 8),
                command=sfn,
            ).pack(side=tk.LEFT, padx=2, ipadx=3, ipady=1)

        section("Internal Notes")
        nf = tk.Frame(sf, bg="#1e293b")
        nf.pack(fill=tk.X, padx=16, pady=4)
        txt_notes = scrolledtext.ScrolledText(
            nf, width=55, height=3, wrap=tk.WORD, bg="#0f172a", fg="#e2e8f0"
        )
        txt_notes.insert("1.0", u.get("admin_notes", "") or "")
        txt_notes.pack()

        def apply():
            try:
                cams = int(e_cams.get().strip())
                rules = int(e_rules.get().strip())
                days = int(e_days.get().strip())
            except ValueError:
                messagebox.showerror(
                    "Error", "Cameras, Rules and Days must be integers."
                )
                return
            payload = {
                "mode": e_mode.get().strip() or "custom",
                "max_cameras": cams,
                "max_blacklist": rules,
                "days": days,
                "features": [k for k, v in fvars.items() if v.get()],
                "admin_notes": txt_notes.get("1.0", tk.END).strip(),
            }
            dlg.destroy()
            self._send_sub(srv_id, username, payload)

        tk.Button(
            dlg,
            text="Apply Custom Plan",
            bg="#2563eb",
            fg="white",
            font=("Arial", 11, "bold"),
            relief=tk.FLAT,
            command=apply,
        ).pack(side=tk.BOTTOM, pady=10, ipadx=16, ipady=4)

    # ── Server management dialog ──────────────────────────────────────────────

    def _servers_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Manage Servers")
        dlg.geometry("560x480")
        dlg.configure(bg="#1e293b")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="Server Configurations",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Arial", 12, "bold"),
        ).pack(pady=(12, 6))
        tk.Label(
            dlg,
            text="Each server requires its own URL and Master Key.\n"
            "Config saved to admin_config.json next to this file.",
            fg="#94a3b8",
            bg="#1e293b",
            font=("Arial", 8),
        ).pack()

        list_frame = tk.Frame(dlg, bg="#1e293b")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        def refresh_list():
            for w in list_frame.winfo_children():
                w.destroy()
            for i, srv in enumerate(self.config["servers"]):
                row = tk.Frame(list_frame, bg="#0f172a", pady=6, padx=8)
                row.pack(fill=tk.X, pady=3)
                tk.Label(
                    row,
                    text=srv["name"],
                    fg=srv.get("color", "white"),
                    bg="#0f172a",
                    font=("Arial", 10, "bold"),
                    width=14,
                    anchor=tk.W,
                ).pack(side=tk.LEFT)
                tk.Label(
                    row,
                    text=srv["base_url"],
                    fg="#64748b",
                    bg="#0f172a",
                    font=("Arial", 8),
                ).pack(side=tk.LEFT, padx=6)
                tk.Button(
                    row,
                    text="Edit",
                    bg="#475569",
                    fg="white",
                    relief=tk.FLAT,
                    command=lambda idx=i: edit_server(idx),
                ).pack(side=tk.RIGHT, padx=3)
                if len(self.config["servers"]) > 1:
                    tk.Button(
                        row,
                        text="Remove",
                        bg="#dc2626",
                        fg="white",
                        relief=tk.FLAT,
                        command=lambda idx=i: remove_server(idx),
                    ).pack(side=tk.RIGHT, padx=3)

        def remove_server(idx):
            if messagebox.askyesno("Confirm", "Remove this server?"):
                self.config["servers"].pop(idx)
                save_config(self.config)
                refresh_list()

        def edit_server(idx=None):
            srv = (
                self.config["servers"][idx]
                if idx is not None
                else {
                    "id": f"server_{len(self.config['servers']) + 1}",
                    "name": "New Server",
                    "base_url": "https://",
                    "master_key": "",
                    "color": "#3b82f6",
                }
            )
            edlg = tk.Toplevel(dlg)
            edlg.title("Edit Server")
            edlg.geometry("400x310")
            edlg.configure(bg="#1e293b")
            edlg.transient(dlg)
            edlg.grab_set()

            fields = {}
            for label, key, default, show in [
                ("Display Name", "name", srv.get("name", ""), None),
                ("Base URL", "base_url", srv.get("base_url", "https://"), None),
                ("Master Key", "master_key", srv.get("master_key", ""), "*"),
                ("Color (hex)", "color", srv.get("color", "#3b82f6"), None),
            ]:
                tk.Label(edlg, text=label + ":", fg="#f8fafc", bg="#1e293b").pack(
                    pady=(8, 2)
                )
                e = ttk.Entry(edlg, width=44, show=show or "")
                e.insert(0, default)
                e.pack()
                fields[key] = e

            def save_server():
                for key, e in fields.items():
                    srv[key] = e.get().strip()
                if idx is None:
                    self.config["servers"].append(srv)
                    self._add_server_tab(srv)
                    # Update create checkboxes
                    var = tk.BooleanVar(value=True)
                    self._create_on[srv["id"]] = var
                else:
                    self.config["servers"][idx] = srv
                    self._server_tabs[srv["id"]]["srv"] = srv
                    self._build_server_tab(srv["id"])
                save_config(self.config)
                edlg.destroy()
                refresh_list()
                self._refresh_all()

            tk.Button(
                edlg,
                text="Save",
                bg="#2563eb",
                fg="white",
                font=("Arial", 10, "bold"),
                command=save_server,
            ).pack(pady=12)

        tk.Button(
            list_frame,
            text="+ Add New Server",
            bg="#2563eb",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=lambda: edit_server(None),
        ).pack(fill=tk.X, pady=(8, 0))

        refresh_list()


if __name__ == "__main__":
    root = tk.Tk()
    app = AdminPCSApp(root)
    root.mainloop()
