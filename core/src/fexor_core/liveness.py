"""Liveness: passive anti-spoof (MiniFASNet) + face mesh metrics for active challenges."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from .face import Face
from .models import ensure_model


def make_session(model_path: Path) -> ort.InferenceSession:
    """CPU by default (small models gain little from GPUs). Override with FEXOR_ORT_PROVIDERS."""
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = int(os.environ.get("FEXOR_ORT_THREADS", min(4, os.cpu_count() or 1)))
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    wanted = os.environ.get("FEXOR_ORT_PROVIDERS", "CPUExecutionProvider").split(",")
    available = set(ort.get_available_providers())
    providers = [p for p in wanted if p in available] or ["CPUExecutionProvider"]
    return ort.InferenceSession(str(model_path), opts, providers=providers)


def _square_crop(image: np.ndarray, face: Face, expansion: float) -> np.ndarray:
    """Square crop around the face box, reflect-padded at image borders."""
    x, y, w, h = face.box
    size = int(max(w, h) * expansion)
    cx, cy = x + w / 2, y + h / 2
    x0, y0 = int(cx - size / 2), int(cy - size / 2)
    ih, iw = image.shape[:2]
    pad = max(0, -x0, -y0, x0 + size - iw, y0 + size - ih)
    if pad:
        image = cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
        x0, y0 = x0 + pad, y0 + pad
    return image[y0 : y0 + size, x0 : x0 + size]


class AntiSpoof:
    """Returns probability that the face is a live person (not print / screen)."""

    SIZE = 128
    EXPANSION = 1.5

    def __init__(self, model_dir: Path | None = None):
        self._sess = make_session(ensure_model("antispoof", model_dir))
        self._input = self._sess.get_inputs()[0].name

    def score(self, image_bgr: np.ndarray, face: Face) -> float:
        crop = _square_crop(image_bgr, face, self.EXPANSION)
        if crop.size == 0:
            return 0.0
        interp = cv2.INTER_LANCZOS4 if crop.shape[0] < self.SIZE else cv2.INTER_AREA
        rgb = cv2.cvtColor(cv2.resize(crop, (self.SIZE, self.SIZE), interpolation=interp),
                           cv2.COLOR_BGR2RGB)
        x = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        logits = self._sess.run(None, {self._input: x})[0][0]
        diff = float(logits[0] - logits[1])  # real - spoof
        return 1.0 / (1.0 + math.exp(-diff))


@dataclass
class MeshMetrics:
    ear: float  # eye aspect ratio, ~0.25-0.35 open, <0.15 closed
    yaw: float  # nose x offset / interocular; + = subject turned to their left
    pitch: float  # nose y offset / interocular; drops when looking up
    smile: float  # mouth width / interocular
    presence: float


# MediaPipe face-mesh indices
_RIGHT_EYE = (33, 160, 158, 133, 153, 144)  # subject's right (image left)
_LEFT_EYE = (362, 385, 387, 263, 373, 380)
_NOSE_TIP, _MOUTH_L, _MOUTH_R, _EYE_R_OUT, _EYE_L_OUT = 1, 291, 61, 33, 263


def _ear(p: np.ndarray, idx: tuple[int, ...]) -> float:
    p1, p2, p3, p4, p5, p6 = (p[i] for i in idx)
    horiz = np.linalg.norm(p1 - p4)
    if horiz < 1e-6:
        return 0.0
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2 * horiz))


def metrics_from_points(pts: np.ndarray, presence: float = 1.0) -> MeshMetrics:
    """pts: (478, 2+) landmarks. Leveled by the outer eye corners, so head roll is ignored."""
    p = pts[:, :2].astype(np.float64)
    d = p[_EYE_L_OUT] - p[_EYE_R_OUT]
    a = -math.atan2(d[1], d[0])
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    p = p @ rot.T
    r_out, l_out = p[_EYE_R_OUT], p[_EYE_L_OUT]
    interocular = float(np.linalg.norm(l_out - r_out)) or 1.0
    mid = (r_out + l_out) / 2
    nose = p[_NOSE_TIP]
    return MeshMetrics(
        ear=(_ear(p, _RIGHT_EYE) + _ear(p, _LEFT_EYE)) / 2,
        yaw=float(nose[0] - mid[0]) / interocular,
        pitch=float(nose[1] - mid[1]) / interocular,
        smile=float(np.linalg.norm(p[_MOUTH_L] - p[_MOUTH_R])) / interocular,
        presence=presence,
    )


class FaceMesh:
    """478-point MediaPipe face mesh on a roll-aligned square crop."""

    SIZE = 256
    EXPANSION = 1.5

    def __init__(self, model_dir: Path | None = None):
        self._sess = make_session(ensure_model("facemesh", model_dir))
        self._input = self._sess.get_inputs()[0].name

    def crop_transform(self, face: Face) -> np.ndarray:
        """2x3 affine mapping image pixels to a roll-aligned 256x256 crop."""
        reye, leye = face.landmarks[0], face.landmarks[1]  # image-left eye, image-right eye
        angle = math.degrees(math.atan2(float(leye[1] - reye[1]), float(leye[0] - reye[0])))
        cx, cy = face.center
        size = max(face.box[2], face.box[3]) * self.EXPANSION
        m = cv2.getRotationMatrix2D((cx, cy), angle, self.SIZE / size)
        m[0, 2] += self.SIZE / 2 - cx
        m[1, 2] += self.SIZE / 2 - cy
        return m

    def run(self, image_bgr: np.ndarray, face: Face) -> tuple[np.ndarray, float]:
        """Landmarks in crop pixels (478, 3) and face presence probability."""
        m = self.crop_transform(face)
        crop = cv2.warpAffine(image_bgr, m, (self.SIZE, self.SIZE), borderMode=cv2.BORDER_REPLICATE)
        x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)[None].astype(np.float32) / 255.0
        out = self._sess.run(None, {self._input: x})
        pts = out[0].reshape(478, 3)
        presence = 1.0 / (1.0 + math.exp(-float(out[1].ravel()[0])))
        return pts, presence

    def metrics(self, image_bgr: np.ndarray, face: Face) -> MeshMetrics:
        pts, presence = self.run(image_bgr, face)
        return metrics_from_points(pts, presence)
