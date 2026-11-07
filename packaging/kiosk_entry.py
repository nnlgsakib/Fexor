"""Entry point of the packaged desktop kiosk app."""

import multiprocessing
import sys

from fexor_kiosk.desktop import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:  # e.g. `Fexor Kiosk.exe enroll --server ... --code ...`
        from fexor_kiosk.main import main

        main()
    else:
        run()
