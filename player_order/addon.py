"""Configure player menus or start their optional automatic recovery."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'resources', 'lib'))
if __name__ == '__main__':
    if sys.argv[1:] == ['startup']:
        from jamieplayerorder.startup import main
    else:
        from jamieplayerorder.manager import main
    main()
