#!/usr/bin/env python3
"""
Diagnostic script pour vérifier l'installation de SLEDI
Exécuter: python check_install.py
"""

import sys

print("=" * 50)
print("SLEDI - Installation Check")
print("=" * 50)

# Check Python version
print(f"\n[1] Python version: {sys.version}")

# Check modules
modules = [
    "flask",
    "flask_sqlalchemy",
    "flask_socketio",
    "flask_limiter",
    "sqlalchemy",
    "requests",
    "PIL",
    "pillow",
]

print("\n[2] Module check:")
all_ok = True
for mod in modules:
    try:
        if mod == "flask_sqlalchemy":
            import flask_sqlalchemy

            print(f"    ✓ flask_sqlalchemy")
        elif mod == "flask_socketio":
            import flask_socketio

            print(f"    ✓ flask_socketio")
        elif mod == "flask_limiter":
            import flask_limiter

            print(f"    ✓ flask_limiter")
        elif mod == "PIL":
            from PIL import Image

            print(f"    ✓ PIL (Pillow)")
        elif mod == "pillow":
            import PIL

            print(f"    ✓ pillow")
        else:
            __import__(mod)
            print(f"    ✓ {mod}")
    except ImportError as e:
        print(f"    ✗ {mod} - NOT INSTALLED")
        all_ok = False
    except Exception as e:
        print(f"    ? {mod} - {e}")

print("\n[3] Testing Flask app import:")
try:
    from app import app

    print("    ✓ app.py imports successfully")

    # Check routes
    print("\n[4] Available routes:")
    for rule in app.url_map.iter_rules():
        if "admin" in str(rule):
            print(f"    {rule.methods} {rule.rule}")

except Exception as e:
    print(f"    ✗ Error importing app: {e}")

print("\n" + "=" * 50)
if all_ok:
    print("Installation OK - Ready to run!")
else:
    print("Missing modules - Run: pip install Flask-SocketIO Pillow")
print("=" * 50)
