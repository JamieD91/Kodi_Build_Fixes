"""User-invoked setup, preview and optional automatic recovery."""
import json
from pathlib import Path
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from jamieplayerorder import ADDON_ID
from jamieplayerorder.chooser import select_player
from jamieplayerorder.integration import inspect, RELATIVE, Unsupported
from jamieplayerorder.control import set_integration
from jamieplayerorder.order import OrderStore

TITLE = 'Player Menu Organiser'


class PlayerListItemCombined:
    """Read-only preview adapter. It cannot start playback."""
    def __init__(self, plugin, name, icon):
        from types import SimpleNamespace
        self.uid = plugin
        self.player = SimpleNamespace(plugin_name=plugin, file='', mode='')
        self.listitem = xbmcgui.ListItem(label=name, label2=plugin)
        self.listitem.setArt({'icon': icon, 'thumb': icon})


def preview_items(helper):
    profile = Path(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.themoviedb.helper/'))
    folders = [profile/'players']
    if helper.getSetting('bundled_players') == 'true':
        folders.append(Path(helper.getAddonInfo('path'))/'resources/players')
    folders.append(profile/'reconfigured_players')
    files = {}
    for folder in folders:
        files.update({p.name: p for p in folder.glob('*.json')})
    players = []
    for path in files.values():
        try:
            meta = json.loads(path.read_text(encoding='utf-8-sig'))
            if str(meta.get('disabled', '')).lower() == 'true':
                continue
            plugins = meta.get('plugin') or []
            plugins = plugins if isinstance(plugins, list) else [plugins]
            if not plugins or not all(xbmc.getCondVisibility('System.AddonIsEnabled('+p+')') for p in plugins):
                continue
            if not any(meta.get(key) for key in ('play_movie', 'play_episode', 'search_movie', 'search_episode')):
                continue
            addon = xbmcaddon.Addon(plugins[0])
            players.append((int(meta.get('priority') or 1000), plugins[0], addon.getAddonInfo('name'), addon.getAddonInfo('icon')))
        except (ValueError, TypeError, RuntimeError):
            xbmc.log('Player Menu Organiser: unsupported player preview file '+path.name, xbmc.LOGWARNING)
    unique = {}
    for _, plugin, name, icon in sorted(players):
        unique.setdefault(plugin, PlayerListItemCombined(plugin, name, icon))
    return list(unique.values())


def main():
    own = xbmcaddon.Addon(ADDON_ID)
    profile = Path(xbmcvfs.translatePath(own.getAddonInfo('profile')))
    store = OrderStore(profile/'order.json')
    try:
        helper = xbmcaddon.Addon('plugin.video.themoviedb.helper')
        helper_path = Path(xbmcvfs.translatePath(helper.getAddonInfo('path')))
        while True:
            try:
                state = inspect((helper_path/RELATIVE).read_text(encoding='utf-8-sig'))
                state = 'Enabled' if state == 'enabled' else 'Not enabled'
            except (Unsupported, OSError, SyntaxError):
                state = 'Changed chooser code - integration unavailable'
            choice = xbmcgui.Dialog().select(TITLE+' - '+state, [
                'Enable / reapply enhanced player menus',
                'Preview and arrange player groups',
                'Put Red Light first',
                'Reset all saved menu orders',
                'Disable enhanced player menus',
                'Automatic recovery settings',
                'Help'])
            if choice < 0:
                return
            if choice in (0, 4):
                if xbmc.Player().isPlaying():
                    xbmcgui.Dialog().ok(TITLE, 'Stop playback before changing the chooser integration.')
                    continue
                if choice == 0 and not xbmcgui.Dialog().yesno(TITLE,
                        'Enable saved ordering and Move up / Move down in TMDb Helper player menus?\n\n'
                        'A small checked chooser change will be backed up. Restart Kodi afterward.'):
                    continue
                changed = set_integration(helper_path, profile, enable=choice == 0)
                if choice == 0 and own.getSetting('auto_reapply') == 'true':
                    xbmc.executebuiltin('RunScript('+ADDON_ID+',startup)')
                xbmcgui.Dialog().ok(TITLE, 'Chooser '+('enabled' if choice == 0 else 'disabled')+
                    ('. Restart Kodi before using it.' if changed else '. No source changes were needed.'))
            elif choice == 1:
                items = preview_items(helper)
                if items:
                    select_player(items, 'Arrange player groups - select or hold for options', preview=True)
                else:
                    xbmcgui.Dialog().ok(TITLE, 'No enabled external players were found in the configured player files.')
            elif choice == 2:
                store.red_light_first()
                xbmcgui.Dialog().ok(TITLE, 'Red Light will appear first in grouped player menus. Reopen any chooser already on screen.')
            elif choice == 3:
                if xbmcgui.Dialog().yesno(TITLE, 'Restore the original order for all player menus?'):
                    store.reset()
            elif choice == 5:
                own.openSettings()
                if own.getSetting('auto_reapply') == 'true':
                    xbmc.executebuiltin('RunScript('+ADDON_ID+',startup)')
            else:
                xbmcgui.Dialog().textviewer(TITLE,
                    'Enable once, then restart Kodi.\n\n'
                    'In a TMDb Helper player chooser, hold Select / OK, press the context-menu key, '
                    'right-click, or use the Move / Order button. Move up, down, to top or bottom saves immediately. '
                    'Normal Select still chooses the player.\n\n'
                    'Player groups and the play/search choices inside each group have separate saved orders. '
                    'New players follow the saved ones; temporarily absent players retain their place. '
                    'Reset this menu restores TMDb Helper ordering for that menu.\n\n'
                    'This affects TMDb Helper player choosers only. It does not reorder unrelated Kodi dialogs. '
                    'No skin files, global keymaps, playback settings or account tokens are changed.\n\n'
                    'Automatic recovery settings lets you choose manual recovery or automatic reapply. '
                    'Automatic mode checks at startup and after Helper updates, waits for playback to stop, '
                    'and restores only a recognised missing hook. An intact hook is not rewritten. '
                    'Disable enhanced player menus also stops automatic recovery. Kodi is never restarted automatically. '
                    'A chooser already loaded by Kodi may need reopening or the next normal restart. '
                    'In manual mode, run Enable / reapply after an update clears the integration. '
                    'Unknown chooser implementations are refused. Saved orders stay in this add-on\'s userdata folder.')
    except Exception as error:
        xbmc.log('Player Menu Organiser: '+type(error).__name__, xbmc.LOGERROR)
        message = str(error) if isinstance(error, Unsupported) else 'Unable to complete this operation ('+type(error).__name__+'). See the Kodi log.'
        xbmcgui.Dialog().ok(TITLE, message)
