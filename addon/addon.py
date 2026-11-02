"""Kodi entry point. All repairs require an explicit menu selection."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
from buildfixes.kodiui import main

if __name__ == '__main__':
    main()
