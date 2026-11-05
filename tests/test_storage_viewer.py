"""Storage accounting, bounded exploration and dependency/skin-reference behavior."""
import contextlib
import importlib
import json
import os
import re
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from support import PROJECT

sys.path.insert(0, str(PROJECT/'storage_viewer/resources/lib'))
from jamiestorageviewer.scan import Root, children, classify, local_roots, normal, scan
from jamiestorageviewer.inventory import Addon, Dependencies, fallback_manifests, from_rpc, read_references


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'kodi'; self.home.mkdir()
        self.roots = [Root(self.home, 'home', 'Kodi home')]

    def put(self, name, raw):
        path = self.home/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if isinstance(raw, bytes) else b'x'*raw)
        return path

    def test_exact_categories_addon_totals_profiles_and_no_content_reads(self):
        files = {
            'addons/plugin.example/addon.xml': 11, 'addons/packages/old.zip': 13,
            'userdata/addon_data/plugin.example/cache.db': 17,
            'userdata/profiles/other/addon_data/plugin.example/cache.db': 19,
            'userdata/Thumbnails/a/image.jpg': 23, 'userdata/Database/MyVideos.db': 29,
            'temp/kodi.log': 31, 'cache/example': 37, 'userdata/guisettings.xml': 41, 'README': 43}
        for name, length in files.items(): self.put(name, length)
        before = {p:p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        with patch('builtins.open', side_effect=AssertionError('Size scan must not read contents')):
            result = scan(self.roots)
        self.assertTrue(result.complete)
        self.assertEqual(result.bytes, sum(files.values()))
        self.assertEqual(result.bytes, sum(result.categories.values()))
        self.assertEqual(result.directories[normal(self.home)].bytes, result.bytes)
        self.assertEqual(result.categories, {'code':11,'packages':13,'data':36,'art':23,
            'database':29,'temp':68,'profile':41,'other':43})
        self.assertEqual(result.addons['plugin.example']['code'], 11)
        self.assertEqual(result.addons['plugin.example']['data'], 36)
        self.assertEqual(len(result.addons['plugin.example']['data_paths']), 2)
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        filtered, _ = children(result, self.home/'addons', category='code')
        self.assertEqual([entry[1] for entry in filtered], ['plugin.example'])

    def test_overlapping_roots_and_unavailable_or_remote_locations(self):
        (self.home/'userdata').mkdir(); (self.home/'temp').mkdir()
        roots, excluded = local_roots([
            (self.home/'userdata','profile','Profile'), (self.home,'home','Kodi home'),
            (self.home/'temp','temp','Temp'), ('smb://server/kodi','home','Remote'),
            ('special://untranslated/','profile','Untranslated'), (self.home/'missing','temp','Missing')])
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].role, 'home')
        self.assertEqual(len(excluded), 3)

    def test_external_profile_is_measured_once_and_classified(self):
        profile = Path(self.temp.name)/'external-profile'
        (profile/'addon_data/a').mkdir(parents=True)
        (profile/'addon_data/a/cache').write_bytes(b'12345')
        roots, _ = local_roots([(self.home,'home','Home'),(profile,'profile','Profile')])
        result = scan(roots)
        self.assertEqual(result.bytes, 5)
        self.assertEqual(result.addons['a']['data'], 5)

    def test_cancellation_returns_consistent_partial_totals(self):
        for n in range(350): self.put('temp/file-'+str(n), 1)
        checks = [0]
        def cancel():
            checks[0] += 1
            return checks[0] >= 3
        result = scan(self.roots, cancel=cancel)
        self.assertFalse(result.complete)
        self.assertGreater(result.files, 0)
        self.assertLess(result.files, 350)
        self.assertEqual(result.bytes, sum(result.categories.values()))
        self.assertEqual(result.bytes, result.directories[normal(self.home)].bytes)

    def test_inaccessible_directory_is_explicit_and_other_data_still_counted(self):
        self.put('bad/file', 10); self.put('good/file', 7)
        original = os.scandir
        def guarded(path):
            if Path(path) == self.home/'bad': raise PermissionError('fixture')
            return original(path)
        with patch('os.scandir', side_effect=guarded): result = scan(self.roots)
        self.assertFalse(result.complete)
        self.assertEqual(result.bytes, 7)
        self.assertEqual(result.error_count, 1)
        self.assertIn('PermissionError', result.errors[0])

    def test_directory_limit_and_windows_junctions_are_not_traversed(self):
        self.put('nested/file', 10)
        result = scan(self.roots, max_directories=1)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.directories), 1)
        junction = NS(path=str(self.home/'junction'), stat=lambda **kw:NS(
            st_mode=stat.S_IFDIR, st_file_attributes=0x400))
        @contextlib.contextmanager
        def entries(path): yield iter([junction])
        with patch('os.scandir', side_effect=entries): result = scan(self.roots)
        self.assertEqual(result.links, 1)
        self.assertEqual(result.bytes, 0)
        self.assertEqual(len(result.directories), 1)

    def test_symbolic_link_to_outside_storage_and_later_link_swap_are_rejected(self):
        outside = Path(self.temp.name)/'outside'; outside.mkdir()
        (outside/'secret').write_bytes(b'x'*1000)
        self.put('folder/original', 5)
        try: (self.home/'linked').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError): self.skipTest('Symbolic-link creation unavailable')
        result = scan(self.roots)
        self.assertEqual(result.bytes, 5)
        self.assertEqual(result.links, 1)
        (self.home/'folder/original').unlink(); (self.home/'folder').rmdir()
        (self.home/'folder').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError): children(result, self.home/'folder')

    def test_hard_link_contents_count_once(self):
        original = self.put('temp/original', 20)
        try: os.link(original, self.home/'temp/second')
        except OSError: self.skipTest('Hard links unavailable')
        result = scan(self.roots)
        self.assertEqual(result.bytes, 20)
        self.assertEqual(result.files, 2)
        self.assertEqual(result.shared_links, 1)

    def test_large_folder_keeps_only_largest_entries_and_top_hundred_files(self):
        for n in range(510): self.put('temp/file-'+str(n), n)
        result = scan(self.roots)
        self.assertEqual(len(result.largest), 100)
        self.assertEqual(result.largest[0][0], 509)
        displayed, omitted = children(result, self.home/'temp')
        self.assertEqual(len(displayed), 500)
        self.assertEqual(omitted, 10)
        self.assertEqual(displayed[-1][0], 10)

    @unittest.skipUnless(os.name == 'nt','Windows directory enumeration metadata')
    def test_windows_folder_browse_refreshes_stale_directory_entry_size(self):
        path=self.put('temp/recent-file',123)
        result=scan(self.roots)
        stale=NS(st_mode=stat.S_IFREG,st_size=0,st_ino=0,st_nlink=0,st_file_attributes=0)
        entry=NS(path=str(path),name=path.name,stat=lambda **kwargs:stale)
        with patch('jamiestorageviewer.scan.os.scandir') as listing:
            listing.return_value.__enter__.return_value=iter([entry])
            rows,omitted=children(result,path.parent)
        self.assertEqual(rows,[(123,path.name,False,str(path))])
        self.assertEqual(omitted,0)

    def test_fallback_manifests_read_bounded_metadata_only(self):
        self.put('addons/a/addon.xml', b'<addon id="a" name="Example" version="1"><requires>'
            b'<import addon="module.required"/><import addon="module.optional" optional="true"/>'
            b'</requires><extension point="xbmc.python.script"/></addon>')
        self.put('userdata/addon_data/a/settings.xml', b'sensitive contents are not XML and must not be read')
        result = scan(self.roots)
        addons = fallback_manifests(result, {})
        self.assertEqual(addons['a'].dependencies, [('module.required',False),('module.optional',True)])
        self.assertIsNone(addons['a'].enabled)

    def test_skin_and_favourites_references_are_bounded_and_do_not_retain_contents(self):
        secret = 'fixture-sensitive-setting-value'
        self.put('addons/skin.test/layout.xml', ('<window><onclick>RunAddon(plugin.b)</onclick><setting>'+secret+'</setting></window>').encode('utf-16'))
        self.put('userdata/favourites.xml', b'<favourites><favourite>plugin://plugin.a/</favourite></favourites>')
        self.put('userdata/addon_data/skin.test/settings.xml', b'<settings><setting>plugin://plugin.b/</setting></settings>')
        graph = Dependencies({'skin.test':Addon('skin.test','Skin',path=str(self.home/'addons/skin.test')),
            'plugin.a':Addon('plugin.a','A'), 'plugin.b':Addon('plugin.b','B')})
        graph.find_references(scan(self.roots), 'skin.test', [self.home/'userdata'])
        self.assertTrue(graph.reference_complete)
        self.assertIn('plugin.a', graph.references); self.assertIn('plugin.b', graph.references)
        self.assertNotIn(secret, str(graph.references))
        self.put('addons/skin.test/oversized.xml', 16*1024*1024+1)
        graph.find_references(scan(self.roots), 'skin.test', [self.home/'userdata'])
        self.assertFalse(graph.reference_complete)

    def test_streamed_skin_links_cross_chunk_boundaries_without_prefix_false_matches(self):
        pattern = re.compile(r'(?<![A-Za-z0-9_.-])(?:plugin.longer|plugin.a)(?![A-Za-z0-9_.-])')
        raw = b'<xml>xxxxx plugin.longer / plugin.a.extra / plugin.a</xml>'
        path = self.put('skin.xml', raw)
        for chunk_size in (9, 16, 32):
            found, used, truncated, cancelled = read_references(path, pattern, 5000, chunk_size=chunk_size)
            self.assertEqual(found, {'plugin.longer','plugin.a'})
            self.assertEqual(used, len(raw))
            self.assertFalse(truncated or cancelled)
        path.write_bytes(b'x'*6+b'plugin.a.extra')
        found, _, _, _ = read_references(path, pattern, 1000, chunk_size=14)
        self.assertEqual(found, set())
        path.write_bytes(b'plugin.a')
        self.assertEqual(read_references(path, pattern, 1000, chunk_size=4)[0], {'plugin.a'})
        path.write_bytes(b'plugin.a.extra')
        self.assertEqual(read_references(path, pattern, 8, chunk_size=4)[0], set())


class DependencyTests(unittest.TestCase):
    def test_direct_indirect_optional_disabled_and_cycles(self):
        graph = Dependencies({
            'a':Addon('a','A',enabled=True,dependencies=[('b',False)]),
            'b':Addon('b','B',enabled=False,dependencies=[('c',False)]),
            'c':Addon('c','C',dependencies=[('b',False)]),
            'd':Addon('d','D',dependencies=[('c',True)])})
        self.assertEqual(graph.dependants('c'), {'a','b'})
        self.assertEqual(graph.dependants('b'), {'a','c'})
        self.assertEqual(graph.optional['c'], {'d'})
        self.assertIn('enabled', graph.status('c'))
        self.assertEqual(graph.status('d'), 'No declared dependants')
        self.assertEqual(graph.status('d','d'), 'Active skin')

    def test_malformed_rpc_marks_inventory_incomplete_and_unknown_optional_is_required(self):
        items = [{'addonid':'valid','name':'Valid','enabled':False,'dependencies':[
            {'addonid':'dep','version':'1','optional':True},{'addonid':'other'}]},
            {'addonid':'../../escape','dependencies':[]},None]
        addons, complete = from_rpc(items)
        self.assertFalse(complete)
        self.assertEqual(set(addons), {'valid'})
        graph = Dependencies(addons, complete)
        self.assertIn('valid', graph.required['other'])
        self.assertEqual(graph.status('valid'), 'Dependency inventory incomplete')


class StorageUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name); (self.home/'userdata').mkdir(); (self.home/'temp').mkdir()
        (self.home/'temp/kodi.log').write_bytes(b'private-log-content')
        self.requests = []; self.builtins = []; self.texts = []; self.choices = iter([-1])
        outer = self
        class Item:
            def __init__(self, **kwargs): self.label = kwargs.get('label','')
            def setArt(self, art): pass
        class Dialog:
            def select(self, *args, **kwargs): return next(outer.choices, -1)
            def textviewer(self, *args): outer.texts.append(args)
            def ok(self, *args): outer.texts.append(args)
        class Progress:
            def create(self,*args): pass
            def update(self,*args): pass
            def close(self): pass
            def iscanceled(self): return False
        mods = {name:ModuleType(name) for name in ('xbmc','xbmcgui','xbmcvfs')}
        def rpc(raw):
            request = json.loads(raw); outer.requests.append(request)
            return json.dumps({'result':{'addons':[{'addonid':'example','name':'Example','enabled':True,'dependencies':[]}]}})
        mods['xbmc'].executeJSONRPC = rpc
        mods['xbmc'].Monitor = lambda:NS(abortRequested=lambda:False)
        mods['xbmc'].getSkinDir = lambda:'skin.builtin'
        mods['xbmc'].executebuiltin = self.builtins.append
        mods['xbmc'].log = lambda *args:None; mods['xbmc'].LOGERROR = 3
        mods['xbmcgui'].Dialog = Dialog; mods['xbmcgui'].DialogProgress = Progress; mods['xbmcgui'].ListItem = Item
        special = {'special://home/':self.home,'special://masterprofile/':self.home/'userdata',
                   'special://profile/':self.home/'userdata','special://temp/':self.home/'temp'}
        mods['xbmcvfs'].translatePath = lambda path:str(special[path])
        self.mock = patch.dict(sys.modules,mods); self.mock.start(); self.addCleanup(self.mock.stop)
        self.ui = importlib.import_module('jamiestorageviewer.ui'); importlib.reload(self.ui)

    def test_native_gui_scan_can_cancel_without_mutations_or_file_contents(self):
        before = {p:p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.ui.main()
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        self.assertEqual([r['method'] for r in self.requests], ['Addons.GetAddons'])
        self.assertFalse(self.builtins)
        self.assertFalse(self.texts)

    def test_overview_states_real_scope_and_partial_dependency_information(self):
        self.choices = iter([0,-1]); self.ui.main()
        text = self.texts[0][1]
        self.assertIn('Android app-private/APK', text)
        self.assertIn('partial / unavailable', text)
        self.assertNotIn('private-log-content', text)

    def test_manager_action_opens_kodi_controls_without_disable_or_uninstall_calls(self):
        self.choices = iter([8]); self.ui.main()
        self.assertEqual(self.builtins, ['ActivateWindow(AddonBrowser,addons://user/all,return)'])
        self.assertTrue(all(r['method'] == 'Addons.GetAddons' for r in self.requests))


if __name__ == '__main__':
    unittest.main()
