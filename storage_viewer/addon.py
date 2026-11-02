"""Open the local Kodi storage viewer. No background service or cleanup jobs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
from jamiestorageviewer.ui import main

if __name__ == '__main__':
    main()
