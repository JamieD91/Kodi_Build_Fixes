"""Automatic recovery remains optional, idempotent and subordinate to user intent."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from support import PROJECT

sys.path.insert(0, str(PROJECT/'player_order/resources/lib'))
from jamieplayerorder.control import Busy, locked, read_intent, set_integration, write_intent
from jamieplayerorder.integration import RELATIVE, inspect, transform
from jamieplayerorder.startup import Recovery


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.helper=self.root/'helper';self.profile=self.root/'profile'
        self.target=self.helper/RELATIVE;self.target.parent.mkdir(parents=True)
        self.source=(PROJECT/'tests/fixtures/player_dialog/standard.py').read_bytes()
        self.target.write_bytes(self.source)
        write_intent(self.profile,True)
        self.order=self.profile/'order.json'
        self.order.write_text('{"schema":1,"scopes":{"providers":["red","umbrella"]}}')
        self.saved_order=self.order.read_bytes()
        self.now=0
        self.recovery=Recovery(self.helper,self.profile,clock=lambda:self.now)

    def backups(self):
        return list(self.profile.glob('backups/*/manifest.json'))

    def settle(self):
        self.assertEqual(self.recovery.step(),'settling')
        self.now+=2
        return self.recovery.step()

    def update(self,raw):
        previous=self.target.stat().st_mtime_ns
        self.target.write_bytes(raw)
        os.utime(self.target,ns=(previous+10**9,previous+10**9))


class RecoveryTests(RecoveryFixture):
    def test_restore_once_repeated_checks_and_another_startup_do_not_write(self):
        self.assertEqual(self.settle(),'restored')
        after=self.target.read_bytes();stamp=self.target.stat().st_mtime_ns
        for _ in range(4):self.assertEqual(self.recovery.step(),'unchanged')
        self.recovery=Recovery(self.helper,self.profile,clock=lambda:self.now)
        self.assertEqual(self.settle(),'enabled')
        self.assertEqual(self.target.read_bytes(),after)
        self.assertEqual(self.target.stat().st_mtime_ns,stamp)
        self.assertEqual(len(self.backups()),1)
        self.assertEqual(self.order.read_bytes(),self.saved_order)

    def test_update_after_startup_is_restored_after_the_file_settles(self):
        self.assertEqual(self.settle(),'restored')
        self.update(self.source+b'\n# unrelated future upstream utility\n')
        self.assertEqual(self.recovery.step(),'settling')
        self.now+=1
        self.update(self.source+b'\n# update still extracting\n')
        self.assertEqual(self.recovery.step(),'settling')
        self.now+=1
        self.assertEqual(self.recovery.step(),'settling')
        self.now+=1
        self.assertEqual(self.recovery.step(),'restored')
        self.assertIn(b'# update still extracting',self.target.read_bytes())
        self.assertEqual(len(self.backups()),2)

    def test_playback_and_manual_mode_defer_without_creating_backups(self):
        self.assertEqual(self.recovery.step(automatic=False),'off')
        self.assertEqual(self.recovery.step(playing=True),'playing')
        self.assertEqual(self.target.read_bytes(),self.source)
        self.assertFalse(self.backups())
        self.assertEqual(self.settle(),'restored')

    def test_explicit_disable_is_not_reversed_and_manual_enable_can_restore_it(self):
        self.assertEqual(self.settle(),'restored')
        set_integration(self.helper,self.profile,False)
        self.assertEqual(self.recovery.step(),'disabled')
        self.assertEqual(inspect(self.target.read_text()),'ready')
        count=len(self.backups())
        self.now+=100
        self.assertEqual(self.recovery.step(),'disabled')
        self.assertEqual(len(self.backups()),count)
        set_integration(self.helper,self.profile,True)
        self.assertTrue(read_intent(self.profile,self.helper))
        self.assertEqual(inspect(self.target.read_text()),'enabled')
        self.assertEqual(self.order.read_bytes(),self.saved_order)

    def test_unknown_updated_code_is_not_patched_and_later_supported_update_recovers(self):
        bad=self.source.replace(b'useDetails=detailed',b'useDetails=True')
        self.update(bad)
        self.assertEqual(self.settle(),'unsupported')
        self.assertEqual(self.recovery.step(),'unchanged')
        self.assertEqual(self.target.read_bytes(),bad)
        self.assertFalse(self.backups())
        self.update(self.source)
        self.assertEqual(self.settle(),'restored')

    def test_migration_remembers_last_old_enable_or_disable_without_touching_order(self):
        for operation in ('enable','disable'):
            (self.profile/'integration.json').unlink()
            folder=self.profile/'backups'/'old';folder.mkdir(parents=True,exist_ok=True)
            (folder/'manifest.json').write_text(json.dumps({'operation':operation}))
            self.assertEqual(read_intent(self.profile,self.helper),operation=='enable')
        self.assertEqual(self.order.read_bytes(),self.saved_order)

    def test_invalid_preferences_and_overlapping_operations_preserve_source(self):
        intent=self.profile/'integration.json';intent.write_text('{broken')
        with self.assertRaises(ValueError):self.recovery.step()
        self.assertEqual(intent.read_text(),'{broken')
        self.assertEqual(self.target.read_bytes(),self.source)
        write_intent(self.profile,True)
        with locked(self.profile):
            self.assertEqual(self.recovery.step(),'busy')
            with self.assertRaises(Busy):set_integration(self.helper,self.profile,False)
        self.assertEqual(self.settle(),'restored')

    def test_missing_helper_during_an_update_is_retried(self):
        self.target.unlink()
        self.assertEqual(self.recovery.step(),'missing')
        self.target.write_bytes(self.source)
        self.assertEqual(self.settle(),'restored')


class ServiceTests(RecoveryFixture):
    def test_off_setting_exits_without_monitor_or_helper_access(self):
        startup=importlib.import_module('jamieplayerorder.startup')
        mods={name:ModuleType(name) for name in ('xbmc','xbmcaddon','xbmcvfs')}
        mods['xbmcaddon'].Addon=lambda addon:NS(getSetting=lambda key:'false')
        with patch.dict(sys.modules,mods),patch.object(startup,'Recovery',side_effect=AssertionError('must not scan')):
            startup.main()
        self.assertEqual(self.target.read_bytes(),self.source)
        self.assertFalse(self.backups())

    def test_service_reapplies_once_and_abort_releases_worker_lock(self):
        startup=importlib.import_module('jamieplayerorder.startup')
        waits=[];logs=[];outer=self
        class Monitor:
            def abortRequested(self):return len(waits)>=3
            def waitForAbort(self,seconds):
                waits.append(seconds);outer.now+=seconds
                return len(waits)>=3
        mods={name:ModuleType(name) for name in ('xbmc','xbmcaddon','xbmcvfs')}
        mods['xbmc'].Monitor=Monitor
        mods['xbmc'].Player=lambda:NS(isPlaying=lambda:False)
        mods['xbmc'].getCondVisibility=lambda condition:True
        mods['xbmc'].log=lambda text,level:logs.append(text)
        mods['xbmc'].LOGINFO=1;mods['xbmc'].LOGWARNING=2
        own=NS(getSetting=lambda key:'true',getAddonInfo=lambda key:str(self.profile))
        helper=NS(getAddonInfo=lambda key:str(self.helper))
        mods['xbmcaddon'].Addon=lambda addon:helper if addon=='plugin.video.themoviedb.helper' else own
        mods['xbmcvfs'].translatePath=lambda path:path
        factory=lambda helper,profile:Recovery(helper,profile,clock=lambda:self.now)
        with patch.dict(sys.modules,mods),patch.object(startup,'Recovery',side_effect=factory),patch.object(startup.time,'monotonic',side_effect=lambda:self.now):
            startup.main()
        self.assertEqual(inspect(self.target.read_text()),'enabled')
        self.assertEqual(len(self.backups()),1)
        self.assertEqual(sum('automatically restored' in line for line in logs),1)
        self.assertEqual(waits,[2,10,10])
        with locked(self.profile,'recovery-service.lock'):pass
