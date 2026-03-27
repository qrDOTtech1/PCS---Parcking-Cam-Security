import tkinter as tk
from tkinter import ttk, messagebox
import requests
import time
import os
from pathlib import Path


def load_master_key():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("MASTER_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


API_BASE_URL = "https://Wlansolo.pythonanywhere.com/api/admin/users"
MASTER_KEY = load_master_key() or "master_key_sledi_2024"
MAX_RETRIES = 3
RETRY_DELAY = 2


def _make_request(method, url, **kwargs):
    global MASTER_KEY
    kwargs.setdefault("timeout", (10, 60))
    kwargs.setdefault("headers", {"X-Master-Key": MASTER_KEY})

    for attempt in range(MAX_RETRIES):
        try:
            res = requests.request(method, url, **kwargs)
            return res
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise


class AdminSlediApp:
    def __init__(self, root):
        self.root = root
        self.root.title("СЛЕДИ (SLEDI) - Admin Control Panel")
        self.root.geometry("600x450")
        self.root.configure(bg="#0f172a")

        self.status_label = None
        self.setup_ui()
        self.refresh_users()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        top_frame = tk.Frame(self.root, bg="#1e293b", pady=15)
        top_frame.pack(fill=tk.X)

        title_lbl = tk.Label(
            top_frame,
            text="СЛЕДИ / SLEDI ADMIN",
            font=("Arial", 16, "bold"),
            fg="#3b82f6",
            bg="#1e293b",
        )
        title_lbl.pack()
        subtitle_lbl = tk.Label(
            top_frame,
            text="Remote Client Management",
            font=("Arial", 10),
            fg="#94a3b8",
            bg="#1e293b",
        )
        subtitle_lbl.pack()

        self.status_label = tk.Label(
            top_frame,
            text="",
            font=("Arial", 9),
            fg="#fbbf24",
            bg="#1e293b",
        )
        self.status_label.pack()

        btn_frame = tk.Frame(top_frame, bg="#1e293b")
        btn_frame.pack(pady=5)

        btn_test = tk.Button(
            btn_frame,
            text="Test Connection",
            bg="#475569",
            fg="white",
            relief=tk.FLAT,
            command=self.test_connection,
        )
        btn_test.pack(side=tk.LEFT, padx=5)

        btn_retry = tk.Button(
            btn_frame,
            text="Retry Load",
            bg="#059669",
            fg="white",
            relief=tk.FLAT,
            command=self.refresh_users,
        )
        btn_retry.pack(side=tk.LEFT, padx=5)

        btn_edit_key = tk.Button(
            btn_frame,
            text="Change Master Key",
            bg="#7c3aed",
            fg="white",
            relief=tk.FLAT,
            command=self.change_master_key,
        )
        btn_edit_key.pack(side=tk.LEFT, padx=5)

        mid_frame = tk.Frame(self.root, bg="#0f172a", pady=20)
        mid_frame.pack(fill=tk.X, padx=20)

        form_frame = tk.Frame(
            mid_frame, bg="#1e293b", bd=1, relief=tk.SOLID, pady=15, padx=15
        )
        form_frame.pack(fill=tk.X)

        tk.Label(
            form_frame,
            text="Create New Client Account",
            font=("Arial", 12, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        ).grid(row=0, column=0, columnspan=2, pady=(0, 10), sticky=tk.W)

        tk.Label(form_frame, text="Username:", fg="#cbd5e1", bg="#1e293b").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.entry_user = ttk.Entry(form_frame, width=30)
        self.entry_user.grid(row=1, column=1, padx=10, pady=5)

        tk.Label(form_frame, text="Password:", fg="#cbd5e1", bg="#1e293b").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )
        self.entry_pass = ttk.Entry(form_frame, width=30, show="*")
        self.entry_pass.grid(row=2, column=1, padx=10, pady=5)

        btn_create = tk.Button(
            form_frame,
            text="Create Account",
            bg="#2563eb",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=self.create_user,
        )
        btn_create.grid(row=3, column=0, columnspan=2, pady=(10, 0), ipadx=10, ipady=3)

        bot_frame = tk.Frame(self.root, bg="#0f172a", pady=10)
        bot_frame.pack(fill=tk.BOTH, expand=True, padx=20)

        header_frame = tk.Frame(bot_frame, bg="#0f172a")
        header_frame.pack(fill=tk.X)
        tk.Label(
            header_frame,
            text="Registered Clients",
            font=("Arial", 12, "bold"),
            fg="#f8fafc",
            bg="#0f172a",
        ).pack(side=tk.LEFT)
        btn_refresh = tk.Button(
            header_frame,
            text="Refresh",
            bg="#475569",
            fg="white",
            relief=tk.FLAT,
            command=self.refresh_users,
        )
        btn_refresh.pack(side=tk.RIGHT)

        columns = ("id", "username", "created_at", "subscription_end")
        self.tree = ttk.Treeview(bot_frame, columns=columns, show="headings", height=6)
        self.tree.heading("id", text="ID")
        self.tree.column("id", width=40, anchor=tk.CENTER)
        self.tree.heading("username", text="Username")
        self.tree.heading("created_at", text="Creation Date")
        self.tree.heading("subscription_end", text="Valid Until")
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

        btn_action_frame = tk.Frame(bot_frame, bg="#0f172a")
        btn_action_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 10))

        btn_delete = tk.Button(
            btn_action_frame,
            text="Delete Client",
            bg="#dc2626",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=self.delete_user,
        )
        btn_delete.pack(
            side=tk.LEFT, ipadx=10, ipady=3, expand=True, fill=tk.X, padx=(0, 5)
        )

        btn_add_month = tk.Button(
            btn_action_frame,
            text="+30 Days Sub",
            bg="#16a34a",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=lambda: self.add_subscription(30),
        )
        btn_add_month.pack(
            side=tk.LEFT, ipadx=10, ipady=3, expand=True, fill=tk.X, padx=5
        )

        btn_add_year = tk.Button(
            btn_action_frame,
            text="+365 Days Sub",
            bg="#059669",
            fg="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            command=lambda: self.add_subscription(365),
        )
        btn_add_year.pack(
            side=tk.RIGHT, ipadx=10, ipady=3, expand=True, fill=tk.X, padx=(5, 0)
        )

    def change_master_key(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Server Settings")
        dialog.geometry("450x220")
        dialog.configure(bg="#1e293b")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Server API URL:", fg="#f8fafc", bg="#1e293b").pack(
            pady=(15, 5)
        )
        entry_url = ttk.Entry(dialog, width=50)
        entry_url.insert(0, API_BASE_URL.replace("/api/admin/users", ""))
        entry_url.pack(pady=5)

        tk.Label(dialog, text="Master Key:", fg="#f8fafc", bg="#1e293b").pack(
            pady=(10, 5)
        )
        entry_key = ttk.Entry(dialog, width=50, show="*")
        entry_key.pack(pady=5)

        tk.Label(
            dialog,
            text="Settings apply to current session only",
            fg="#94a3b8",
            bg="#1e293b",
            font=("Arial", 8),
        ).pack()

        def save_settings():
            global MASTER_KEY, API_BASE_URL
            new_key = entry_key.get().strip()
            new_url = entry_url.get().strip().rstrip("/")
            if new_key:
                MASTER_KEY = new_key
            if new_url:
                API_BASE_URL = f"{new_url}/api/admin/users"
            self.status_label.config(text="Settings updated for session", fg="#22c55e")
            dialog.destroy()

        btn_save = tk.Button(
            dialog, text="Apply", bg="#2563eb", fg="white", command=save_settings
        )
        btn_save.pack(pady=15)

    def test_connection(self):
        self.status_label.config(text="Testing...", fg="#fbbf24")
        self.root.update()
        try:
            res = _make_request("GET", API_BASE_URL)
            if res.status_code in [200, 401]:
                self.status_label.config(text="Server OK", fg="#22c55e")
                messagebox.showinfo(
                    "OK", f"Server responding!\nStatus: {res.status_code}"
                )
            else:
                self.status_label.config(text="Server error", fg="#ef4444")
                messagebox.showinfo("Status", f"Server: {res.status_code}")
        except Exception as e:
            self.status_label.config(text="Connection failed", fg="#ef4444")
            messagebox.showerror("Failed", f"Cannot connect to server.\n\n{e}")

    def refresh_users(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        self.status_label.config(text="Loading...", fg="#fbbf24")
        self.root.update()

        try:
            res = _make_request("GET", API_BASE_URL)
            if res.status_code == 200:
                data = res.json()
                for u in data.get("users", []):
                    self.tree.insert(
                        "",
                        tk.END,
                        values=(
                            u["id"],
                            u["username"],
                            u["created_at"],
                            u.get("subscription_end", "Unlimited"),
                        ),
                    )
                self.status_label.config(
                    text=f"Loaded {len(data.get('users', []))} clients", fg="#22c55e"
                )
            elif res.status_code == 401:
                self.status_label.config(text="Auth error", fg="#ef4444")
                messagebox.showerror("Auth Error", "Invalid MASTER_KEY.")
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", f"Server: {res.status_code}")
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", f"Could not connect:\n{e}")

    def create_user(self):
        user = self.entry_user.get().strip()
        pwd = self.entry_pass.get().strip()

        if not user or not pwd:
            messagebox.showwarning("Validation", "Username and password required.")
            return

        self.status_label.config(text="Creating...", fg="#fbbf24")
        self.root.update()

        try:
            res = _make_request(
                "POST", API_BASE_URL, json={"username": user, "password": pwd}
            )
            data = res.json()
            if res.status_code == 201:
                self.status_label.config(text="User created!", fg="#22c55e")
                messagebox.showinfo("Success", f"Client '{user}' created!")
                self.entry_user.delete(0, tk.END)
                self.entry_pass.delete(0, tk.END)
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", f"Could not connect:\n{e}")

    def delete_user(self):
        selected = self.tree.focus()
        if not selected:
            messagebox.showwarning("Selection", "Select a client first.")
            return

        values = self.tree.item(selected, "values")
        username = values[1]

        if not messagebox.askyesno("Confirm", f"Delete '{username}' and ALL data?"):
            return

        self.status_label.config(text="Deleting...", fg="#fbbf24")
        self.root.update()

        try:
            res = _make_request("DELETE", f"{API_BASE_URL}/{username}")
            data = res.json()
            if res.status_code == 200:
                self.status_label.config(text="Deleted!", fg="#22c55e")
                messagebox.showinfo("Success", f"Client '{username}' deleted.")
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", f"Could not connect:\n{e}")

    def add_subscription(self, days):
        selected = self.tree.focus()
        if not selected:
            messagebox.showwarning("Selection", "Select a client first.")
            return

        values = self.tree.item(selected, "values")
        username = values[1]

        self.status_label.config(text="Updating...", fg="#fbbf24")
        self.root.update()

        try:
            res = _make_request(
                "POST", f"{API_BASE_URL}/{username}/subscription", json={"days": days}
            )
            data = res.json()
            if res.status_code == 200:
                self.status_label.config(text="Updated!", fg="#22c55e")
                messagebox.showinfo(
                    "Success", data.get("message", "Subscription updated.")
                )
                self.refresh_users()
            else:
                self.status_label.config(text="Error", fg="#ef4444")
                messagebox.showerror("Error", data.get("message", "Unknown error"))
        except Exception as e:
            self.status_label.config(text="Failed", fg="#ef4444")
            messagebox.showerror("Network Error", f"Could not connect:\n{e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = AdminSlediApp(root)
    root.mainloop()
