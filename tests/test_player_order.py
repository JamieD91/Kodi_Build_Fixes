"""Saved-order behavior, real Helper selection contracts and Kodi callbacks."""
import ast
from functools import cached_property
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from support import PROJECT

sys.path.insert(0, str(PROJECT/'player_order/resources/lib'))
from jamieplayerorder.order import OrderStore, describe
from jamieplayerorder.integration import ORIGINAL, PATCHED, RELATIVE, apply, inspect, transform, Unsupported


class PlayerListItem:
    def __init__(self, player, posx):
        self.player, self.posx = player, posx
        self.uid = player.plugin_name
        self.listitem = NS(label=player.name)


class PlayerListItemCombined(PlayerListItem):
    pass


def item(plugin, index=0, file='select.json', mode='play_episode', grouped=True):
    player = NS(plugin_name=plugin, file=file, mode=mode, name=plugin)
    return (PlayerListItemCombined if grouped else PlayerListItem)(player, index)


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'order.json'
        self.store = OrderStore(self.path)

    def test_red_light_first_survives_reload_and_unknown_players_follow(self):
        items = [item('plugin.video.umbrella'), item('plugin.video.redlight'), item('new.player')]
        scope, rows = describe(items)
        self.assertEqual([r.index for r in self.store.ordered(scope, rows)], [0, 1, 2])
        self.store.red_light_first()
        self.assertEqual([r.index for r in OrderStore(self.path).ordered(scope, rows)], [1, 0, 2])
        self.assertEqual(items[0].player.plugin_name, 'plugin.video.umbrella')

    def test_move_visible_entries_preserves_absent_players_and_other_menus(self):
        self.store.save_visible('providers', ['a', 'missing', 'b'])
        self.store.save_visible('methods:a', ['play', 'search'])
        self.store.save_visible('providers', ['b', 'a', 'new'])
        self.assertEqual(self.store.read()['scopes'], {
            'providers': ['b', 'missing', 'a', 'new'], 'methods:a': ['play', 'search']})
        self.store.reset('providers')
        self.assertEqual(self.store.read()['scopes'], {'methods:a': ['play', 'search']})

    def test_method_identity_is_stable_across_labels_and_list_positions(self):
        original = [item('a', 4, mode='play_movie', grouped=False), item('a', 12, mode='search_movie', grouped=False)]
        scope, rows = describe(original)
        self.assertEqual(scope, 'methods:a')
        self.store.save_visible(scope, [rows[1].key, rows[0].key])
        original[0].player.name = 'translated / coloured / renamed label'
        original[0].posx = 99
        _, fresh = describe(original)
        self.assertEqual([r.index for r in self.store.ordered(scope, fresh)], [1, 0])

    def test_invalid_preferences_and_duplicate_identities_are_not_overwritten(self):
        self.path.write_text('{broken')
        with self.assertRaises(ValueError): self.store.red_light_first()
        self.assertEqual(self.path.read_text(), '{broken')
        with self.assertRaises(ValueError): describe([item('same'), item('same')])


class IntegrationTests(unittest.TestCase):
    def source(self):
        return ('from xbmcgui import Dialog\nfrom tmdbhelper.lib.addon.plugin import get_localized\n'
                'class PlayerSelectStandard:\n    @staticmethod\n'+ORIGINAL+
                '\n    def unrelated(self):\n        return "preserved"\n').encode()

    def test_enable_disable_is_idempotent_preserves_other_methods_and_crlf_bom(self):
        before = b'\xef\xbb\xbf'+self.source().replace(b'\n', b'\r\n')
        after = transform(before, True)
        self.assertEqual(inspect(after.decode('utf-8-sig')), 'enabled')
        self.assertEqual(transform(after, True), after)
        self.assertEqual(transform(after, False), before)
        self.assertNotIn(b'\n', after.replace(b'\r\n', b''))
        self.assertIn(b'return "preserved"', after)

    def test_changed_chooser_and_decorator_are_refused(self):
        for before in [self.source().replace(b'32042', b'99999'),
                       self.source().replace(b'@staticmethod', b'@classmethod'),
                       self.source().replace(b'useDetails=detailed', b'useDetails=True')]:
            with self.assertRaises(Unsupported): transform(before, True)

    def test_future_version_with_unchanged_contract_accepts_comments(self):
        before = self.source().replace(b'class Player', b'# version 99.0.0\nclass Player')
        before = before.replace(b'        x = Dialog()', b'        # harmless new comment\n        x = Dialog()')
        after = transform(before, True)
        self.assertIn(b'# version 99.0.0', after)
        self.assertEqual(inspect(after.decode()), 'enabled')

    def test_backup_enable_and_disable_modify_only_the_checked_source(self):
        with tempfile.TemporaryDirectory() as folder:
            helper, profile = Path(folder)/'helper', Path(folder)/'own-profile'
            target = helper/RELATIVE; target.parent.mkdir(parents=True)
            target.write_bytes(self.source())
            unrelated = helper/'player.json'; unrelated.write_text('preserve')
            self.assertTrue(apply(helper, profile))
            backups = list(profile.glob('backups/*/standard.py'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), self.source())
            self.assertFalse(apply(helper, profile))
            # Another method can change independently; disable removes only
            # our recognised method, preserving that later unrelated update.
            target.write_bytes(target.read_bytes().replace(b'return "preserved"', b'return "updated"'))
            self.assertTrue(apply(helper, profile, enable=False))
            self.assertIn(b'return "updated"', target.read_bytes())
            self.assertEqual(inspect(target.read_text()), 'ready')
            self.assertEqual(unrelated.read_text(), 'preserve')

    def test_normal_and_index_selection_keep_original_input_coordinates(self):
        # Exercise the complete injected function, including disabled/missing
        # add-on fallback and normal posx versus combined-group index return.
        xbmc = ModuleType('xbmc'); addon = ModuleType('xbmcaddon')
        enabled = [True]; logs = []
        xbmc.getCondVisibility = lambda condition: enabled[0]
        xbmc.log = lambda *args: logs.append(args); xbmc.LOGERROR = 3
        addon.Addon = lambda own: NS(getAddonInfo=lambda key: str(PROJECT/'player_order'))
        chooser = ModuleType('jamieplayerorder.chooser'); chooser.select_player = lambda *args: 1
        dialog = NS(select=lambda *args, **kwargs: 0, notification=lambda *args, **kwargs: None)
        with patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcaddon': addon, 'jamieplayerorder.chooser': chooser}):
            namespace = {'Dialog': lambda: dialog, 'get_localized': lambda key: 'Choose action'}
            exec(compile('class PlayerSelectStandard:\n    @staticmethod\n'+PATCHED, '<real-hook>', 'exec'), namespace)
            choose = namespace['PlayerSelectStandard'].select_player
            rows = [item('umbrella', 8), item('redlight', 37)]
            self.assertEqual(choose(rows), 37)
            self.assertEqual(choose(rows, index=True), 1)
            chooser.select_player = lambda *args: -1
            self.assertEqual(choose(rows), -1)
            enabled[0] = False
            self.assertEqual(choose(rows), 8)
            enabled[0] = True
            chooser.select_player = lambda *args: (_ for _ in ()).throw(RuntimeError('fixture failure'))
            self.assertEqual(choose(rows), 8)
            self.assertTrue(logs)


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.context_choices = iter([]); self.modal_action = lambda window: window.close()
        self.shown = []; self.logs = []
        outer = self
        class Control:
            def __init__(self): self.position = 0; self.items = []; self.visible = True
            def setLabel(self, label): self.label = label
            def setVisible(self, visible): self.visible = visible
            def reset(self): self.items = []
            def addItems(self, items): self.items.extend(items)
            def selectItem(self, index): self.position = index
            def getSelectedPosition(self): return self.position
        class Window:
            def __new__(cls, *args, **kwargs):
                obj = super().__new__(cls)
                obj.controls = {key: Control() for key in (1, 3, 4, 5, 6, 7)}
                obj.focus = 6; obj.closed = False
                return obj
            def getControl(self, key): return self.controls[key]
            def setFocusId(self, key): self.focus = key
            def getFocusId(self): return self.focus
            def close(self): self.closed = True
            def doModal(self):
                self.onInit(); outer.shown.append(self)
                outer.modal_action(self)
        class Dialog:
            def contextmenu(self, options):
                outer.last_options = options
                return next(outer.context_choices, -1)
            def ok(self, *args): pass
        mods = {name: ModuleType(name) for name in ('xbmc', 'xbmcaddon', 'xbmcgui', 'xbmcvfs')}
        mods['xbmcgui'].WindowXMLDialog = Window; mods['xbmcgui'].Dialog = Dialog
        mods['xbmcaddon'].Addon = lambda own: NS(getAddonInfo=lambda key:
            str(outer.profile) if key == 'profile' else str(PROJECT/'player_order'))
        mods['xbmcvfs'].translatePath = lambda path: path
        mods['xbmc'].LOGERROR = 3; mods['xbmc'].LOGWARNING = 2
        mods['xbmc'].log = lambda *args: outer.logs.append(args)
        mods['xbmc'].getCondVisibility = lambda condition: True
        self.mock_modules = patch.dict(sys.modules, mods); self.mock_modules.start()
        self.addCleanup(self.mock_modules.stop)
        self.ui = importlib.import_module('jamieplayerorder.chooser'); importlib.reload(self.ui)
        self.store = OrderStore(self.profile/'order.json')

    def test_sorted_click_returns_original_row_not_display_position(self):
        self.store.red_light_first()
        self.modal_action = lambda window: window.onClick(6)
        result = self.ui.select_player([item('plugin.video.umbrella'), item('plugin.video.redlight')], 'Choose action')
        self.assertEqual(result, 1)
        self.assertEqual(self.shown[0].controls[6].items[0].label, 'plugin.video.redlight')

    def test_context_move_saves_then_select_still_returns_original_player(self):
        def interact(window):
            window.controls[6].selectItem(1)
            window.onAction(NS(getId=lambda: 117))
            self.assertEqual(window.controls[6].getSelectedPosition(), 0)
            window.onClick(6)
        self.context_choices = iter([0])  # Move up from second entry
        self.modal_action = interact
        self.assertEqual(self.ui.select_player([item('a'), item('b')], 'Choose action'), 1)
        self.assertEqual(OrderStore(self.profile/'order.json').read()['scopes']['providers'], ['b', 'a'])

    def test_cancel_context_or_back_does_not_choose_or_save(self):
        def interact(window):
            window.onAction(NS(getId=lambda: 117))
            window.onAction(NS(getId=lambda: 92))
        self.modal_action = interact
        self.assertEqual(self.ui.select_player([item('a'), item('b')], 'Choose action'), -1)
        self.assertFalse(self.store.path.exists())

    def test_reset_restores_native_order_and_preview_never_returns_a_player(self):
        self.store.red_light_first()
        self.context_choices = iter([2])  # first row: down, bottom, reset
        def interact(window):
            window.onClick(6)  # preview click opens arrangement, never playback
            self.assertEqual(window.rows[0].plugin, 'plugin.video.umbrella')
            self.assertEqual(window.selected, -1)
            window.onClick(7)
        self.modal_action = interact
        self.assertEqual(self.ui.select_player([item('plugin.video.umbrella'), item('plugin.video.redlight')],
            'Preview', preview=True), -1)
        self.assertNotIn('providers', self.store.read()['scopes'])

    def test_real_helper_combined_and_inner_menus_resolve_the_selected_method(self):
        namespace = {'PlayerListItem': PlayerListItem, 'PlayerListItemCombined': PlayerListItemCombined,
                     'get_localized': lambda key: 'Choose action', 'Dialog': lambda: None}
        fixture = PROJECT/'tests/fixtures/player_dialog'
        for filename in ('standard.py', 'combined.py'):
            source = (fixture/filename).read_bytes()
            if filename == 'standard.py': source = transform(source, True)
            tree = ast.parse(source)
            tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
            exec(compile(tree, filename, 'exec'), namespace)
        players = [item(plugin, n, mode=mode, grouped=False).player
                   for n, (plugin, mode) in enumerate([
                       ('plugin.video.umbrella', 'play_episode'), ('plugin.video.umbrella', 'search_episode'),
                       ('plugin.video.redlight', 'play_episode'), ('plugin.video.redlight', 'search_episode')])]
        self.store.red_light_first()
        scope, rows = describe([PlayerListItem(p, n) for n, p in enumerate(players[2:])])
        self.store.save_visible(scope, [rows[1].key, rows[0].key])
        self.modal_action = lambda window: window.onClick(6)
        chosen = namespace['PlayerSelectCombined'](NS(items=players)).select()
        self.assertIs(chosen, players[3])
        self.assertEqual(len(self.shown), 2)
        self.assertEqual(self.shown[0].rows[0].plugin, 'plugin.video.redlight')
        self.assertEqual(self.shown[1].rows[0].item.player.mode, 'search_episode')


if __name__ == '__main__':
    unittest.main()
