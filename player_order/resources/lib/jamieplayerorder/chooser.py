"""Use the active skin's select layout, adding a context menu and persistence."""
from pathlib import Path
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from jamieplayerorder import ADDON_ID
from jamieplayerorder.order import OrderStore, describe


class PlayerOrderDialog(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.rows = kwargs['rows']
        self.scope = kwargs['scope']
        self.store = kwargs['store']
        self.title = kwargs['title']
        self.detailed = kwargs['detailed']
        self.preview = kwargs.get('preview', False)
        self.selected = -1
        self.failure = None
        self.list_id = 6 if self.detailed else 3

    def onInit(self):
        try:
            self.getControl(1).setLabel(self.title)
            self.getControl(3 if self.detailed else 6).setVisible(False)
            self.getControl(self.list_id).setVisible(True)
            self.getControl(5).setLabel('Move / Order...')
            self.getControl(5).setVisible(True)
            self.getControl(7).setLabel('Close' if self.preview else 'Cancel')
            self.getControl(7).setVisible(True)
            self.getControl(4).setVisible(False)
            self.refresh()
        except Exception as error:
            self.failure = type(error).__name__
            self.close()

    def refresh(self, selected=0):
        control = self.getControl(self.list_id)
        control.reset()
        control.addItems([row.listitem for row in self.rows])
        if self.rows:
            control.selectItem(min(selected, len(self.rows)-1))
        self.setFocusId(self.list_id)

    def onClick(self, control_id):
        if control_id == 7:
            self.close()
        elif control_id == 5:
            self.arrange()
        elif control_id == self.list_id:
            if self.preview:
                self.arrange()
                return
            position = self.getControl(self.list_id).getSelectedPosition()
            if 0 <= position < len(self.rows):
                self.selected = self.rows[position].index
                self.close()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (10, 92):
            self.close()
        elif action_id in (117, 101):  # ContextMenu / right mouse button
            if self.getFocusId() in (self.list_id, 5):
                self.arrange()

    def arrange(self):
        current = self.getControl(self.list_id).getSelectedPosition()
        if not 0 <= current < len(self.rows):
            return
        actions = []
        if current > 0:
            actions.extend([('Move up', current-1), ('Move to top', 0)])
        if current < len(self.rows)-1:
            actions.extend([('Move down', current+1), ('Move to bottom', len(self.rows)-1)])
        actions.append(('Reset this menu to original order', None))
        choice = xbmcgui.Dialog().contextmenu([label for label, _ in actions])
        if choice < 0:
            return
        destination = actions[choice][1]
        try:
            if destination is None:
                self.store.reset(self.scope)
                self.rows = sorted(self.rows, key=lambda row: row.index)
                destination = 0
            else:
                changed = list(self.rows)
                changed.insert(destination, changed.pop(current))
                self.store.save_visible(self.scope, [row.key for row in changed])
                self.rows = changed
            self.refresh(destination)
        except Exception as error:
            xbmc.log('Jamie Player Menu Organiser: order save failed ('+type(error).__name__+')', xbmc.LOGERROR)
            xbmcgui.Dialog().ok('Player Menu Organiser', 'The order could not be saved. Existing preferences have been preserved.')


def select_player(items, title, detailed=True, preview=False):
    if not items:
        return -1
    own = xbmcaddon.Addon(ADDON_ID)
    store = OrderStore(Path(xbmcvfs.translatePath(own.getAddonInfo('profile')))/'order.json')
    scope, rows = describe(items)
    rows = store.ordered(scope, rows)
    path = xbmcvfs.translatePath(own.getAddonInfo('path'))
    for filename in ('DialogSelect.xml', 'JamiePlayerOrder.xml'):
        try:
            window = PlayerOrderDialog(
                filename, path, 'Default', '1080i', rows=rows, scope=scope,
                store=store, title=title, detailed=detailed, preview=preview)
            try:
                window.doModal()
                result, failure = window.selected, window.failure
            finally:
                del window
        except Exception as error:
            result, failure = -1, type(error).__name__
        if failure is None:
            return result
        xbmc.log('Jamie Player Menu Organiser: unsupported select layout ('+failure+')', xbmc.LOGWARNING)
    raise RuntimeError('Player order dialog could not be opened.')
