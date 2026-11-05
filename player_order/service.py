"""Run the optional player-menu recovery service when Kodi starts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
from jamieplayerorder.startup import main

if __name__ == '__main__':
    main()
