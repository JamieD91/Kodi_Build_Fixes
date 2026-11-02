"""Finish only cleanup queued by the user in a previous Kodi session."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
from buildfixes.kodiui import finish_startup

if __name__ == '__main__':
    finish_startup()
