FROM python:3.11-slim

WORKDIR /app

# Dépendances système pour opencv-python-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1-mesa-glx \
    libxcb1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Installer PyTorch CPU-only (évite ~700MB de CUDA inutile)
RUN pip install --no-cache-dir \
    torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Pré-télécharger les modèles YOLO + EasyOCR dans l'image
RUN python download_models.py

EXPOSE 5000

# 1 seul worker : le moteur ANPR charge ~1GB en RAM par worker
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --worker-class eventlet --timeout 120 app:app
