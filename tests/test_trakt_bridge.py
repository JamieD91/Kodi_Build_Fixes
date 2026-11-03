"""Synthetic credentials only: provider relocation and ReAuthorize distribution."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from support import make_engine
from buildfixes.engine import Incompatible, Plan
from buildfixes.trakt_bridge import (
    BASE, KEYS, VARIABLES, SYNC, LEGACY, REPAIRED, patch_keys, patch_sync, inspect_bridge)

MASTER, ORIGINAL, CUSTOM = 'a'*64, 'b'*64, 'c'*64


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root/KEYS
        self.key.parent.mkdir(parents=True)
        self.key.write_text("CLIENT_ID = '"+ORIGINAL+"'\n")
        self.settings = {'plugin.video.themoviedb.helper':{}, 'script.module.tmdbhelper.sync.trakt':{}}
        self.cleared = []

    def runtime(self):
        outer = self
        class Addon:
            def __init__(self, addon): self.values = outer.settings[addon]
            def getSetting(self, name): return self.values.get(name, '')
            def setSetting(self, name, value): self.values[name] = value
        helper = self.root/'addons/plugin.video.themoviedb.helper'; helper.mkdir(parents=True,exist_ok=True)
        legacy = helper/'legacy.py'
        context = {'current':['TMDb Helper'],'os':os,
            'var':NS(chk_tmdbh=str(helper),addons=str(self.root/'addons'),tmdbh_client=ORIGINAL,
                client_am=MASTER,path_tmdbh=str(legacy),tmdbh_secret='d'*64,secret_am='e'*64),
            'xbmcvfs':NS(exists=lambda path:Path(path).exists()),'xbmcaddon':NS(Addon=Addon),
            'xbmcgui':NS(Window=lambda window:NS(clearProperty=self.cleared.append)),
            'your_token':'synthetic-access','your_refresh':'synthetic-refresh','your_expires':'604900',
            'accountmgr':object(),'_trakt_lifetime':lambda settings:604800}
        exec(compile(REPAIRED,'reauthorize-test','exec'),context)
        return legacy

    def test_runtime_configures_relocated_key_and_both_token_stores(self):
        self.runtime()
        self.assertEqual(ast.literal_eval(ast.parse(self.key.read_text()).body[0].value),MASTER)
        helper=self.settings['plugin.video.themoviedb.helper']['trakt_token']
        provider=self.settings['script.module.tmdbhelper.sync.trakt']['token']
        self.assertEqual(helper,provider)
        self.assertEqual(json.loads(provider)['created_at'],100)
        self.assertEqual(json.loads(provider)['expires_in'],604800)
        self.assertEqual(len(self.cleared),5)
        self.cleared.clear(); self.runtime()
        self.assertFalse(self.cleared)

    def test_changed_master_token_is_distributed_even_when_legacy_store_matches(self):
        self.runtime()
        self.settings['script.module.tmdbhelper.sync.trakt']['token']='stale-provider-value'
        self.runtime()
        self.assertEqual(self.settings['plugin.video.themoviedb.helper']['trakt_token'],
            self.settings['script.module.tmdbhelper.sync.trakt']['token'])

    def test_custom_or_changed_key_module_refuses_before_credentials_are_written(self):
        for value in ["CLIENT_ID = '"+CUSTOM+"'\n", "CLIENT_ID = get_custom_key()\n", "CLIENT_ID = '"+ORIGINAL+"'\nother = True\n"]:
            self.key.write_text(value)
            with self.assertRaises(RuntimeError):self.runtime()
            self.assertEqual(self.key.read_text(),value)
            self.assertEqual(self.settings['script.module.tmdbhelper.sync.trakt'],{})

    def test_failed_atomic_key_write_preserves_key_and_does_not_copy_credentials(self):
        before=self.key.read_bytes()
        with patch('os.replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.runtime()
        self.assertEqual(self.key.read_bytes(),before)
        self.assertFalse(list(self.key.parent.glob('keys.reauthorize-*')))
        self.assertEqual(self.settings['script.module.tmdbhelper.sync.trakt'],{})

    def test_legacy_runtime_location_still_works(self):
        self.key.unlink()
        helper=self.root/'addons/plugin.video.themoviedb.helper';helper.mkdir(parents=True)
        legacy=helper/'legacy.py';legacy.write_text("CLIENT_ID = '"+ORIGINAL+"'\n")
        self.runtime()
        self.assertIn(MASTER,legacy.read_text())
        self.assertIn('trakt_token',self.settings['plugin.video.themoviedb.helper'])
        self.assertNotIn('token',self.settings['script.module.tmdbhelper.sync.trakt'])

    def test_source_patches_preserve_unrelated_code_bom_crlf_and_are_idempotent(self):
        raw=b'\xef\xbb\xbf'+("# Future version 99\r\nCLIENT_ID = '"+ORIGINAL+"'\r\n").encode()
        changed=patch_keys(raw,MASTER,ORIGINAL)
        self.assertTrue(changed.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(patch_keys(changed,MASTER,ORIGINAL),changed)
        self.assertIn(b'# Future version 99\r\n',changed)
        source=('class Auth:\n    def trakt_auth(self):\n'+
            '\n'.join('        '+line for line in LEGACY.splitlines())+'\n\ndef unrelated():\n    return 42\n').encode()
        changed=patch_sync(source)
        self.assertEqual(patch_sync(changed),changed)
        self.assertIn(b'def unrelated():\n    return 42',changed)
        with self.assertRaises(Incompatible):patch_sync(source.replace(b'if False:',b'if True:'))

    def test_plan_requires_matching_signin_and_known_api_contract(self):
        engine=make_engine(self.root/'fixture')
        def write(name,text):
            path=engine.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        write(KEYS,"CLIENT_ID = '"+ORIGINAL+"'\n")
        write(VARIABLES,"def traktID():\n    return '"+MASTER+"'\ntmdbh_client = '"+ORIGINAL+"'\n")
        write(BASE+'api/api.py','from tmdbhelper_sync_trakt.api.keys import CLIENT_ID\nclass TraktAPI:\n    client_id = CLIENT_ID\n')
        write(SYNC,'class Auth:\n    def trakt_auth(self):\n'+
            '\n'.join('        '+line for line in LEGACY.splitlines())+'\n')
        engine.backend.set_setting('script.module.reauthorize','trakt.token','synthetic-access')
        engine.backend.set_setting('script.module.reauthorize','trakt.refresh','synthetic-refresh')
        engine.backend.set_setting('script.module.tmdbhelper.sync.trakt','token',json.dumps(
            {'access_token':'synthetic-access','refresh_token':'synthetic-refresh'}))
        plan=Plan('reauthorize','ReAuthorize')
        inspect_bridge(engine,plan)
        self.assertEqual(len(plan.files),2)
        plan.status='ready'
        engine.apply(plan)
        again=Plan('reauthorize','ReAuthorize');inspect_bridge(engine,again)
        self.assertFalse(again.files)
        engine.backend.set_setting('script.module.tmdbhelper.sync.trakt','token',json.dumps({'access_token':'other','refresh_token':'other'}))
        with self.assertRaises(Incompatible):inspect_bridge(engine,Plan('reauthorize','ReAuthorize'))


if __name__ == '__main__':unittest.main()
