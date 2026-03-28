"""
PCS — Alert Clip Generator
Crée un GIF animé à partir des dernières frames du buffer caméra
pour l'envoyer dans les alertes menace (Telegram).
"""

import io
import logging
import numpy as np

logger = logging.getLogger(__name__)


def create_alert_gif(frame_buffers, camera_id, max_frames=20):
    """
    Crée un GIF animé à partir des dernières frames du buffer.

    Args:
        frame_buffers: dict {camera_id: deque(JPEG bytes)}
        camera_id: ID de la caméra
        max_frames: nombre max de frames à inclure

    Returns:
        bytes du GIF ou None si échec
    """
    from PIL import Image
    import cv2

    buffer = frame_buffers.get(camera_id)
    if not buffer:
        return None

    frames_to_use = list(buffer)[-max_frames:]
    if len(frames_to_use) < 2:
        return None

    pil_frames = []
    for frame_bytes in frames_to_use:
        try:
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w = img_rgb.shape[:2]
            if w > 320:
                scale = 320 / w
                img_rgb = cv2.resize(img_rgb, (320, int(h * scale)))
            pil_frames.append(Image.fromarray(img_rgb))
        except Exception:
            continue

    if len(pil_frames) < 2:
        return None

    try:
        gif_buffer = io.BytesIO()
        pil_frames[0].save(
            gif_buffer,
            format="GIF",
            save_all=True,
            append_images=pil_frames[1:],
            duration=200,
            loop=0,
            optimize=True,
        )
        gif_buffer.seek(0)
        gif_bytes = gif_buffer.getvalue()
        logger.info(f"[AlertClip] GIF created: {len(pil_frames)} frames, {len(gif_bytes)} bytes")
        return gif_bytes
    except Exception as e:
        logger.error(f"[AlertClip] GIF creation failed: {e}")
        return None
