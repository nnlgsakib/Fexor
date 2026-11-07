"""Model registry: pinned download URLs + sha256, cached on disk.

All models are licensed for commercial use (MIT / Apache-2.0).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    filename: str
    url: str
    sha256: str
    license: str


MODELS: dict[str, ModelSpec] = {
    # Face detector, 5 landmarks. MIT.
    "yunet": ModelSpec(
        "face_detection_yunet_2023mar.onnx",
        "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "MIT",
    ),
    # Face recognizer, 128-d embedding. Apache-2.0.
    "sface": ModelSpec(
        "face_recognition_sface_2021dec.onnx",
        "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "Apache-2.0",
    ),
    # Passive anti-spoof (MiniFASNetV2-SE, int8), real/spoof logits. Apache-2.0.
    "antispoof": ModelSpec(
        "antispoof_minifas_int8.onnx",
        "https://raw.githubusercontent.com/facenox/face-antispoof-onnx/"
        "2f8dd93dd0fd9d120f08e83d9020719f67ffdb68/models/best_model_quantized.onnx",
        "fde20585635cae62ed1d41796f76b6f8bc4b92cd91ec1cf0f1bc6485d2d587a9",
        "Apache-2.0",
    ),
    # MediaPipe Face Mesh V2, 478 landmarks, converted to ONNX. Apache-2.0.
    "facemesh": ModelSpec(
        "facemesh_478.onnx",
        "https://huggingface.co/senty-au/face_landmarks_detector-ONNX/resolve/"
        "337d58218b5b1cc597ca3c67360880b920f6ce7b/onnx/model.onnx",
        "7d6e82dee82a1dca5fbddb282b3cc74571833a530de317fc22ae325c3358beeb",
        "Apache-2.0",
    ),
}


def default_model_dir() -> Path:
    return Path(os.environ.get("FEXOR_MODEL_DIR", Path.home() / ".cache" / "fexor" / "models"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(name: str, model_dir: Path | None = None) -> Path:
    """Return path to a verified model file, downloading it if missing."""
    spec = MODELS[name]
    model_dir = model_dir or default_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / spec.filename
    if path.exists() and _sha256(path) == spec.sha256:
        return path

    fd, tmp_name = tempfile.mkstemp(dir=model_dir, suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        # URLs are pinned https constants above, never user input.
        urllib.request.urlretrieve(spec.url, tmp)  # noqa: S310
        digest = _sha256(tmp)
        if digest != spec.sha256:
            raise RuntimeError(f"checksum mismatch for {name}: {digest}")
        # mkstemp makes the file owner-only (0600). Models are public files that a service user
        # other than the downloader must read (Docker image, Linux packages), so open it up.
        tmp.chmod(0o644)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def ensure_all(model_dir: Path | None = None) -> dict[str, Path]:
    return {name: ensure_model(name, model_dir) for name in MODELS}


if __name__ == "__main__":
    for n, p in ensure_all().items():
        print(f"{n}: {p}")
