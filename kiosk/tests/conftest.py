import os

# Tests talk to the kiosk app through Starlette's TestClient, whose Host is "testserver".
# The real app only answers 127.0.0.1 / localhost (blocks DNS rebinding).
os.environ.setdefault("FEXOR_KIOSK_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")
