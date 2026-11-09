import os

# Refuse absurd image sizes before decoding (a small PNG can expand to gigabytes of pixels).
# OpenCV reads this once, on its first decode, so it must be set before anything decodes.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(40_000_000))
