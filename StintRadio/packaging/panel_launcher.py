"""Entry point of the packaged KerbApex.exe: the control panel."""
import sys

from stintradio.gui import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
