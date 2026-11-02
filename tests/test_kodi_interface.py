from pathlib import Path
import importlib
import importlib.util
import io
import json
import sys
import tempfile
from types import ModuleType,SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zipfile
from support import make_engine,PROJECT


class KodiInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('interface_builder',PROJECT/'tools/build.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        cls.package=builder.outputs()['repository/script.jamied91.buildfixes/script.jamied91.buildfixes-0.1.0.zip']

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=make_engine(Path(self.temp.name)/'fixture')
        self.install=Path(self.temp.name)/'install';self.install.mkdir()
        with zipfile.ZipFile(io.BytesIO(self.package)) as z:z.extractall(self.install)
        self.addon_path=self.install/'script.jamied91.buildfixes'
        self.text=[];self.builtins=[];self.choices=iter([-1]);self.confirm=False
        outer=self
        class Addon:
            def __init__(self,addon=None):self.id=addon or 'script.jamied91.buildfixes'
            def getAddonInfo(self,key):
                if key=='id':return self.id
                if key=='path':return str(outer.addon_path)
                if key=='profile':return str(outer.engine.profile)
                if key=='name':return 'Jamie Build Fixes'
                return ''
            def getSetting(self,key):
                if self.id=='script.jamied91.buildfixes':return ''
                return outer.engine.backend.get_setting(self.id,key)
            def setSetting(self,key,value):outer.engine.backend.set_setting(self.id,key,value)
            def openSettings(self):pass
        class Dialog:
            def select(self,*a):return next(outer.choices,-1)
            def multiselect(self,*a):return [0]
            def yesno(self,*a):return outer.confirm
            def textviewer(self,*a):outer.text.append(a)
            def ok(self,*a):outer.text.append(a)
        mods={name:ModuleType(name) for name in ('xbmc','xbmcaddon','xbmcgui','xbmcvfs')}
        mods['xbmc'].Player=lambda:NS(isPlaying=lambda:False)
        mods['xbmc'].executebuiltin=self.builtins.append
        mods['xbmc'].Monitor=lambda:NS(waitForAbort=lambda seconds:False)
        mods['xbmc'].log=lambda *a:None
        mods['xbmc'].LOGINFO=1;mods['xbmc'].LOGWARNING=2
        mods['xbmcaddon'].Addon=Addon;mods['xbmcgui'].Dialog=Dialog
        mods['xbmcvfs'].translatePath=lambda p:str(self.engine.root) if p=='special://home/' else p
        self.modules=patch.dict(sys.modules,mods);self.modules.start();self.addCleanup(self.modules.stop)
        self.ui=importlib.import_module('buildfixes.kodiui');importlib.reload(self.ui)

    def test_menu_can_cancel_without_touching_addons(self):
        self.ui.main()
        self.assertFalse((self.engine.profile/'backups').exists())

    def test_scan_dialog_uses_real_packaged_catalog(self):
        self.choices=iter([0,-1]);self.ui.main()
        self.assertEqual(len(self.text),1)
        shown=self.text[0][1]
        self.assertIn('Trakt calendar episode playback',shown)
        self.assertIn('Needs repair',shown)
        self.assertIn('ReAuthorize',shown)
        self.assertFalse((self.engine.profile/'backups').exists())

    def test_cancelled_apply_makes_no_changes(self):
        self.choices=iter([2,-1]);self.confirm=False
        before={p.relative_to(self.engine.root):p.read_bytes() for p in self.engine.root.rglob('*') if p.is_file()}
        self.ui.main()
        after={p.relative_to(self.engine.root):p.read_bytes() for p in self.engine.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertFalse((self.engine.profile/'backups').exists())

    def test_update_action_uses_native_repository_check(self):
        self.choices=iter([4,-1]);self.ui.main()
        self.assertEqual(self.builtins,['UpdateAddonRepos'])

    def test_startup_does_not_patch_or_run_cleanup_in_the_same_session(self):
        self.engine.queue_jobs(['calendar_cache'])
        before=(self.engine.profile/'pending.json').read_bytes()
        self.ui.finish_startup()
        self.assertEqual((self.engine.profile/'pending.json').read_bytes(),before)
        self.assertFalse((self.engine.profile/'backups').exists())


if __name__=='__main__':unittest.main()
