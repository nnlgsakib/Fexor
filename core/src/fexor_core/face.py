"""Face detection (YuNet), alignment + embedding (SFace), quality gates."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .models import ensure_model

MODEL_ID = "sface-2021dec"
EMBEDDING_DIM = 128
# OpenCV's published cosine threshold for SFace (LFW). Spaces can raise it.
DEFAULT_MATCH_THRESHOLD = 0.363


@dataclass
class Face:
    """One detected face. Coordinates are in source-image pixels."""

    raw: np.ndarray  # YuNet row (15 floats), needed by SFace alignCrop
    box: tuple[float, float, float, float]  # x, y, w, h
    # 5 points: subject's right eye, left eye, nose tip, right mouth, left mouth
    landmarks: np.ndarray  # (5, 2)
    score: float

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.box
        return x + w / 2, y + h / 2


@dataclass
class Quality:
    ok: bool
    face_px: float
    sharpness: float
    brightness: float
    frontal: float  # 0 = perfectly frontal, ~0.5 = strong profile
    reasons: list[str] = field(default_factory=list)


@dataclass
class QualityLimits:
    min_face_px: float = 80
    min_sharpness: float = 25.0
    min_brightness: float = 40.0
    max_brightness: float = 220.0
    max_frontal: float = 0.22


class FaceEngine:
    """Thread-safe wrapper. OpenCV DNN objects are not safe for concurrent use."""

    def __init__(self, model_dir: Path | None = None, score_threshold: float = 0.85):
        self._lock = threading.Lock()
        self._detector = cv2.FaceDetectorYN.create(
            str(ensure_model("yunet", model_dir)), "", (320, 320), score_threshold, 0.3, 20
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(ensure_model("sface", model_dir)), "")
        self._input_size: tuple[int, int] | None = None

    def detect(self, image: np.ndarray) -> list[Face]:
        h, w = image.shape[:2]
        with self._lock:
            if self._input_size != (w, h):
                self._detector.setInputSize((w, h))
                self._input_size = (w, h)
            _, rows = self._detector.detect(image)
        if rows is None:
            return []
        faces = [
            Face(
                raw=row.copy(),
                box=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
                landmarks=row[4:14].reshape(5, 2).astype(np.float32),
                score=float(row[14]),
            )
            for row in rows
        ]
        faces.sort(key=lambda f: f.box[2] * f.box[3], reverse=True)
        return faces

    def align(self, image: np.ndarray, face: Face) -> np.ndarray:
        """112x112 aligned BGR crop."""
        with self._lock:
            return self._recognizer.alignCrop(image, face.raw)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        with self._lock:
            feat = self._recognizer.feature(aligned)
        return normalize(feat.reshape(-1).astype(np.float32))

    def embed(self, image: np.ndarray, face: Face) -> np.ndarray:
        return self.embed_aligned(self.align(image, face))


def normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(normalize(a), normalize(b)))


def best_match(probe: np.ndarray, templates: list[np.ndarray]) -> float:
    return max((cosine(probe, t) for t in templates), default=-1.0)


def frontal_score(face: Face) -> float:
    """Horizontal nose offset from eye midpoint, relative to eye distance."""
    reye, leye, nose = face.landmarks[0], face.landmarks[1], face.landmarks[2]
    eye_dist = float(np.linalg.norm(leye - reye))
    if eye_dist < 1:
        return 1.0
    mid = (reye + leye) / 2
    return abs(float(nose[0] - mid[0])) / eye_dist


def assess(aligned: np.ndarray, face: Face, limits: QualityLimits | None = None) -> Quality:
    limits = limits or QualityLimits()
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    face_px = min(face.box[2], face.box[3])
    frontal = frontal_score(face)
    reasons = []
    if face_px < limits.min_face_px:
        reasons.append("face_too_small")
    if sharpness < limits.min_sharpness:
        reasons.append("blurry")
    if brightness < limits.min_brightness:
        reasons.append("too_dark")
    if brightness > limits.max_brightness:
        reasons.append("too_bright")
    if frontal > limits.max_frontal:
        reasons.append("not_frontal")
    return Quality(not reasons, face_px, sharpness, brightness, frontal, reasons)


def decode_image(data: bytes) -> np.ndarray:
    try:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as e:  # e.g. over OPENCV_IO_MAX_IMAGE_PIXELS
        raise ValueError("image too large or damaged") from e
    if img is None:
        raise ValueError("not a decodable image")
    return img


def limit_size(image: np.ndarray, max_side: int = 1280) -> np.ndarray:
    h, w = image.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1:
        return image
    return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


@dataclass
class EnrollResult:
    embedding: np.ndarray | None
    quality: Quality | None
    error: str | None


def enroll_from_image(
    engine: FaceEngine, image: np.ndarray, limits: QualityLimits | None = None
) -> EnrollResult:
    """Exactly one good-quality face must be present."""
    image = limit_size(image)
    faces = engine.detect(image)
    if not faces:
        return EnrollResult(None, None, "no_face")
    if len(faces) > 1:
        # Allow tiny background faces; reject if a second face is comparable in size.
        a0 = faces[0].box[2] * faces[0].box[3]
        a1 = faces[1].box[2] * faces[1].box[3]
        if a1 > a0 * 0.25:
            return EnrollResult(None, None, "multiple_faces")
    face = faces[0]
    aligned = engine.align(image, face)
    q = assess(aligned, face, limits)
    if not q.ok:
        return EnrollResult(None, q, ",".join(q.reasons))
    return EnrollResult(engine.embed_aligned(aligned), q, None)
