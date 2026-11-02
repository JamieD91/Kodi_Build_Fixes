"""Manually enable and configure the TMDb Helper player-menu organiser."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
from jamieplayerorder.manager import main

if __name__ == '__main__':
    main()
