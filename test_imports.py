#!/usr/bin/env python3
print("Checking imports...")
try:
    import flask_socketio

    print("OK: flask_socketio")
except:
    print("MISSING: flask_socketio")

try:
    from PIL import Image

    print("OK: Pillow")
except:
    print("MISSING: Pillow")

try:
    import flask

    print("OK: Flask")
except:
    print("MISSING: Flask")

print("Done")
