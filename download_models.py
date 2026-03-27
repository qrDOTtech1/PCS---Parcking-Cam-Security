"""
PCS — Pre-download YOLO & EasyOCR models.
Run during Docker build to bake weights into the image.

Usage:
    python download_models.py
"""

print("[PCS] Downloading YOLOv11-nano model...")
from ultralytics import YOLO

model = YOLO("yolo11n.pt")
print(f"[PCS] YOLOv11-nano ready: {model.model_name}")

print("[PCS] Downloading EasyOCR English model...")
import easyocr

reader = easyocr.Reader(["en"], gpu=False, verbose=True, download_enabled=True)
print("[PCS] EasyOCR ready.")

print("[PCS] All models downloaded successfully.")
