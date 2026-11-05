"""Opt-in recovery of the existing chooser hook; never restarts Kodi."""
from pathlib import Path
import time
from jamieplayerorder.control import Busy, locked, read_intent
from jamieplayerorder.integration import apply, RELATIVE, Unsupported


def stamp(path):
    info = path.stat()
    return info.st_mtime_ns, info.st_size, info.st_ino


class Recovery:
    def __init__(self, helper, profile, clock=time.monotonic):
        self.helper, self.profile = Path(helper), Path(profile)
        self.clock = clock
        self.candidate = self.checked = None
        self.since = 0

    def step(self, automatic=True, playing=False):
        if not automatic:
            return 'off'
        if playing:
            return 'playing'
        try:
            with locked(self.profile):
                if not read_intent(self.profile, self.helper):
                    return 'disabled'
                current = stamp(self.helper/RELATIVE)
                if current == self.checked:
                    return 'unchanged'
                if current != self.candidate:
                    self.candidate, self.since = current, self.clock()
                    return 'settling'
                if self.clock()-self.since < 2:
                    return 'settling'
                try:
                    changed = apply(self.helper, self.profile, enable=True)
                except (Unsupported, SyntaxError):
                    self.checked = current
                    return 'unsupported'
                self.checked = stamp(self.helper/RELATIVE)
                return 'restored' if changed else 'enabled'
        except FileNotFoundError:
            self.candidate = self.checked = None
            return 'missing'
        except (Unsupported, SyntaxError):
            return 'unsupported'
        except Busy:
            return 'busy'


def main():
    import xbmc
    import xbmcaddon
    import xbmcvfs
    from jamieplayerorder import ADDON_ID
    own = xbmcaddon.Addon(ADDON_ID)
    # Manual mode exits immediately: no loop, Helper scan, migration or backups.
    if own.getSetting('auto_reapply') != 'true':
        return
    profile = Path(xbmcvfs.translatePath(own.getAddonInfo('profile')))
    monitor = xbmc.Monitor()
    try:
        with locked(profile, 'recovery-service.lock'):
            xbmc.log('Jamie Player Menu Organiser: automatic recovery started', xbmc.LOGINFO)
            started = time.monotonic()
            recovery = None
            last_problem = None
            while not monitor.abortRequested():
                if own.getSetting('auto_reapply') != 'true':
                    return
                try:
                    if not xbmc.getCondVisibility('System.AddonIsEnabled(plugin.video.themoviedb.helper)'):
                        state = 'missing'
                    else:
                        helper = xbmcaddon.Addon('plugin.video.themoviedb.helper')
                        path = Path(xbmcvfs.translatePath(helper.getAddonInfo('path')))
                        if recovery is None or recovery.helper != path:
                            recovery = Recovery(path, profile)
                        state = recovery.step(playing=xbmc.Player().isPlaying())
                    if state == 'disabled':
                        return
                    if state == 'restored':
                        xbmc.log('Jamie Player Menu Organiser: chooser integration automatically restored', xbmc.LOGINFO)
                    if state == 'unsupported' and last_problem != (state, recovery.checked):
                        last_problem = (state, recovery.checked)
                        xbmc.log('Jamie Player Menu Organiser: changed chooser code; automatic repair skipped', xbmc.LOGWARNING)
                except (OSError, ValueError, RuntimeError) as error:
                    state = 'error'
                    problem = type(error).__name__
                    if last_problem != problem:
                        last_problem = problem
                        xbmc.log('Jamie Player Menu Organiser: automatic recovery deferred ('+problem+')', xbmc.LOGWARNING)
                # Check startup updates promptly, then just one small file per minute.
                delay = 2 if state == 'settling' else (10 if time.monotonic()-started < 120 else 60)
                if monitor.waitForAbort(delay):
                    return
    except Busy:
        return  # Kodi's service and a Settings launch must not create two workers.
    except OSError as error:
        xbmc.log('Jamie Player Menu Organiser: recovery service could not start ('+type(error).__name__+')', xbmc.LOGWARNING)
