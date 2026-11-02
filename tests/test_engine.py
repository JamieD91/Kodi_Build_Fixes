from pathlib import Path
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from support import make_engine, CATALOG, PROJECT
from buildfixes.engine import Incompatible, Busy, patch_python, canonical, safe_path, sha
from buildfixes.cachejobs import calendar_cache, guide_retry, trakt_metadata, process_pending


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine=make_engine(self.temp.name)

    def plan(self,which):return self.engine.scan([which])[0]

    def test_all_original_repairs_apply_and_rescan_idempotently(self):
        plans=self.engine.scan()
        self.assertEqual(len(plans),10)
        for p in plans:self.assertEqual(p.status,'ready',(p.id,p.reason))
        for p in plans:self.engine.apply(p)
        for p in self.engine.scan():self.assertEqual(p.status,'fixed',(p.id,p.reason))
        self.assertEqual(self.engine.backend.get_setting('plugin.video.playlistbrowser','unrelated'),'preserve-me')
        self.assertEqual(len(self.engine.backups()),10)

    def test_newer_version_and_unrelated_new_code_are_preserved(self):
        path=self.engine.root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/monitor/player.py'
        text=path.read_text()+'\n# A future release can add unrelated utilities.\ndef future_helper():\n    return 42\n'
        path.write_text(text)
        p=self.plan('tmdb_player')
        self.assertEqual(p.status,'ready')
        self.engine.apply(p)
        self.assertIn('def future_helper():',path.read_text())
        self.assertEqual(self.plan('tmdb_player').status,'fixed')

    def test_changed_implementation_is_refused_without_writes(self):
        path=self.engine.root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/monitor/player.py'
        path.write_text(path.read_text().replace('    def get_playingitem(self):','    def get_playingitem(self):\n        raise ValueError("upstream changed")'))
        before=path.read_bytes()
        p=self.plan('tmdb_player')
        self.assertEqual(p.status,'incompatible')
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.engine.profile/'backups').exists())

    def test_source_changed_after_scan_is_refused(self):
        p=self.plan('autowidget')
        path=self.engine.root/p.files[0].path
        path.write_bytes(path.read_bytes()+b'\n# update arrived\n')
        with self.assertRaises(Busy):self.engine.apply(p)

    def test_failed_multi_file_write_rolls_back(self):
        p=self.plan('autowidget')
        originals={f.path:f.before for f in p.files}
        real=self.engine.atomic_write
        count=[0]
        def fail_second(relative,data):
            count[0]+=1
            if count[0]==2:raise OSError('simulated disk error')
            real(relative,data)
        with patch.object(self.engine,'atomic_write',side_effect=fail_second):
            with self.assertRaises(OSError):self.engine.apply(p)
        for relative,raw in originals.items():self.assertEqual(self.engine.read(relative),raw)
        record=json.loads(self.engine.backups()[0].read_text())
        self.assertEqual(record['status'],'rolled_back')

    def test_undo_restores_exact_originals(self):
        p=self.plan('autowidget')
        folder=self.engine.apply(p)
        self.engine.undo(folder/'manifest.json')
        for f in p.files:self.assertEqual(self.engine.read(f.path),f.before)
        self.assertEqual(self.plan('autowidget').status,'ready')

    def test_undo_refuses_a_later_addon_update(self):
        p=self.plan('tmdb_player');folder=self.engine.apply(p)
        path=self.engine.root/p.files[0].path
        path.write_bytes(path.read_bytes()+b'\n# newer addon\n')
        newer=path.read_bytes()
        with self.assertRaises(Incompatible):self.engine.undo(folder/'manifest.json')
        self.assertEqual(path.read_bytes(),newer)

    def test_interrupted_transaction_can_be_recovered(self):
        p=self.plan('autowidget');folder=self.engine.apply(p)
        manifest=folder/'manifest.json';record=json.loads(manifest.read_text());record['status']='prepared'
        manifest.write_text(json.dumps(record))
        self.engine.atomic_write(p.files[0].path,p.files[0].before)
        self.engine.undo(manifest)
        for f in p.files:self.assertEqual(self.engine.read(f.path),f.before)

    def test_live_settings_undo_preserves_other_preferences(self):
        p=self.plan('liveguide');folder=self.engine.apply(p)
        self.assertEqual(self.engine.backend.get_setting('plugin.video.playlistbrowser','DLHD_base'),'https://thedaddy.pk')
        self.engine.undo(folder/'manifest.json')
        self.assertEqual(self.engine.backend.get_setting('plugin.video.playlistbrowser','DLHD_base'),'https://dlive.sx')
        self.assertEqual(self.engine.backend.get_setting('plugin.video.playlistbrowser','unrelated'),'preserve-me')

    def test_playback_blocks_apply(self):
        p=self.plan('calendar');self.engine.backend.playing=True
        with self.assertRaises(Busy):self.engine.apply(p)

    def test_path_escape_and_symlink_are_refused(self):
        for value in ('../secret','/tmp/secret','addons/../../secret','C:/secret','addons\\bad'):
            with self.assertRaises(Incompatible):safe_path(self.engine.root,value)
        link=self.engine.root/'addons/link'
        try:link.symlink_to(Path(self.temp.name),target_is_directory=True)
        except OSError:return
        with self.assertRaises(Incompatible):safe_path(self.engine.root,'addons/link/private')

    def test_comment_docstring_crlf_and_bom_changes_are_supported(self):
        path=self.engine.root/'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/monitor/player.py'
        text=path.read_text().replace('    def get_playingitem(self):','    def get_playingitem(self):\n        """New upstream documentation."""\n        # comment')
        path.write_bytes(b'\xef\xbb\xbf'+text.replace('\n','\r\n').encode())
        p=self.plan('tmdb_player');self.assertEqual(p.status,'ready',p.reason)
        self.engine.apply(p)
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n',path.read_bytes())

    def test_recognised_newer_weather_implementation_is_not_replaced(self):
        root=self.engine.root/'addons/weather.multi/lib'
        (root/'providers/yahoo.py').write_text('def weather(label):\n    return get_outlook(label)\n')
        (root/'conversions.py').write_text("OUTLOOK_LOWER = {key.lower(): value for key, value in OUTLOOK.items()}\nCONDITION_LOWER = {key.lower(): value for key, value in CONDITION.items()}\ndef get_outlook(label):\n    return OUTLOOK_LOWER.get(label.lower(), 'na')\ndef get_condition(label):\n    return CONDITION_LOWER.get(label.lower(), label)\n")
        self.assertEqual(self.plan('weather').status,'fixed')

    def test_a_trailing_slash_in_the_old_provider_does_not_block_repair(self):
        self.engine.backend.set_setting('plugin.video.playlistbrowser','DLHD_base','https://dlive.sx/')
        plan=self.plan('liveguide')
        self.assertEqual(plan.status,'ready')
        self.engine.apply(plan)
        self.assertEqual(self.engine.backend.get_setting('plugin.video.playlistbrowser','DLHD_base'),'https://thedaddy.pk')


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=make_engine(self.temp.name)

    def test_calendar_cache_preserves_unrelated_rows(self):
        path=self.engine.root/'userdata/addon_data/plugin.video.themoviedb.helper/database_07/ItemContainer.db'
        path.parent.mkdir(parents=True,exist_ok=True)
        con=sqlite3.connect(path)
        con.execute('CREATE TABLE simplecache(id TEXT PRIMARY KEY, expires INTEGER, data BLOB, checksum TEXT)')
        rows=[('ListTraktCalendar_my',1,b'old','a'),('ListCalendar_old',2,b'old2','b'),
              ('TraktData_calendars',3,b'raw-api-data','c'),('OtherList',4,b'preserve','d')]
        con.executemany('INSERT INTO simplecache VALUES(?,?,?,?)',rows);con.commit();con.close()
        listings=self.engine.root/'temp/archive_cache';listings.mkdir(parents=True)
        (listings/'calendar.fi').write_bytes(b'plugin.video.themoviedb.helper/?info=trakt_calendar')
        (listings/'other.fi').write_bytes(b'plugin://other/?info=watched')
        result=calendar_cache(self.engine)
        self.assertEqual(result,{'calendar_rows':2,'calendar_listings':1})
        con=sqlite3.connect(path)
        self.assertEqual(con.execute('SELECT * FROM simplecache ORDER BY id').fetchall(),sorted(rows[2:]))
        self.assertEqual(con.execute('PRAGMA integrity_check').fetchone(),('ok',));con.close()
        self.assertTrue((listings/'other.fi').exists())

    def test_unknown_calendar_cache_schema_is_not_written(self):
        path=self.engine.root/'userdata/addon_data/plugin.video.themoviedb.helper/database_07/ItemContainer.db'
        path.parent.mkdir(parents=True,exist_ok=True)
        con=sqlite3.connect(path);con.execute('CREATE TABLE simplecache(new_column TEXT)');con.commit();con.close()
        before=path.read_bytes()
        with self.assertRaises(Incompatible):calendar_cache(self.engine)
        self.assertEqual(path.read_bytes(),before)

    def test_cleanup_waits_for_restart_and_only_runs_once(self):
        self.engine.queue_jobs(['calendar_cache'])
        self.assertEqual(process_pending(self.engine),{'restart_required':True})
        path=self.engine.profile/'pending.json';data=json.loads(path.read_text());data['origin_pid']=-1;path.write_text(json.dumps(data))
        process_pending(self.engine)
        self.assertFalse(path.exists())
        self.assertEqual(process_pending(self.engine),{})

    def test_guide_retry_reset_waits_for_an_export(self):
        base=self.engine.root/'userdata/addon_data/plugin.video.playlistbrowser/dlhd';base.mkdir(parents=True)
        state=base/'refresh-state.json';state.write_text('{"retry_after":999999,"last_success":123,"path":"keep"}')
        (base/'export.lock').mkdir()
        with self.assertRaises(Busy):guide_retry(self.engine)
        (base/'export.lock').rmdir();guide_retry(self.engine)
        self.assertEqual(json.loads(state.read_text()),{'retry_after':0,'last_success':123,'path':'keep'})

    def token_fixture(self, expiry=None):
        b=self.engine.backend
        expiry=int(time.time())+604800 if expiry is None else expiry
        for key,value in {'trakt.token':'fixture-access','trakt.refresh':'fixture-refresh','trakt.expires':str(expiry),'trakt.lifetime':'604800'}.items():b.set_setting('script.module.reauthorize',key,value)
        original={'access_token':'fixture-access','refresh_token':'fixture-refresh','created_at':expiry,'expires_in':7776000,'scope':'public','extra':'keep'}
        b.set_setting('script.trakt','authorization',json.dumps(original))
        return original,expiry

    def test_metadata_correction_preserves_credentials_and_unknown_fields(self):
        old,expiry=self.token_fixture();trakt_metadata(self.engine)
        new=json.loads(self.engine.backend.get_setting('script.trakt','authorization'))
        self.assertEqual(new,dict(old,created_at=expiry-604800,expires_in=604800))
        audit=(self.engine.profile/'trakt-metadata-audit.json').read_text()
        self.assertNotIn('fixture-access',audit);self.assertNotIn('fixture-refresh',audit)

    def test_metadata_does_not_replace_different_or_due_tokens(self):
        old,_=self.token_fixture(expiry=int(time.time())+10)
        with self.assertRaises(Busy):trakt_metadata(self.engine)
        self.assertEqual(json.loads(self.engine.backend.get_setting('script.trakt','authorization')),old)
        old,_=self.token_fixture()
        old['refresh_token']='different-fixture-refresh'
        self.engine.backend.set_setting('script.trakt','authorization',json.dumps(old))
        trakt_metadata(self.engine)
        self.assertEqual(json.loads(self.engine.backend.get_setting('script.trakt','authorization')),old)


if __name__=='__main__':unittest.main()
