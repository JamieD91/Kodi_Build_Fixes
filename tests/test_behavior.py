from pathlib import Path
from functools import cached_property
from datetime import datetime
import ast
import copy
import json
import tempfile
import unittest
from urllib.parse import urlencode,parse_qs
from support import make_engine
import runtime_cases as cases


class RuntimeBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.engine=make_engine(cls.temp.name)
        for plan in cls.engine.scan():
            if plan.status!='ready':raise AssertionError((plan.id,plan.reason))
            cls.engine.apply(plan)
        cases.STAGED=cls.engine.root/'addons'

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_tmdb_player_real_callbacks(self):cases.tmdb_player()
    def test_autowidget_real_callbacks(self):cases.autowidget()
    def test_autowidget_real_queue(self):cases.widget_queue()
    def test_umbrella_real_progress_mapping(self):cases.umbrella()
    def test_context_route_preserves_library_items_and_rejects_unsafe_ids(self):cases.context()
    def test_scraper_null_and_cached_results(self):cases.scraper()
    def test_weather_case_variants_and_unknown_icons(self):cases.weather()
    def test_live_guide_actual_scheduler(self):cases.scheduler_checks()

    def test_calendar_actual_item_dictionary_and_play_url(self):
        root=self.engine.root/'addons'
        files=[
            'plugin.video.themoviedb.helper/resources/tmdbhelper/lib/items/directories/mapper_basic.py',
            'plugin.video.themoviedb.helper/resources/tmdbhelper/lib/items/directories/mapper_standard.py',
            'script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar_base.py',
            'script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar.py']
        env={'cached_property':cached_property,'convert_timestamp':lambda *a,**k:datetime(2026,11,2,12),
             'get_region_date':lambda d,f:d.isoformat() if d else ''}
        for rel in files:
            tree=ast.parse((root/rel).read_text())
            tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
            exec(compile(tree,'real-calendar-mappers','exec'),env)
        for season,episode in ((2,5),(0,1),(1,0),(12,123)):
            fixture={'show':{'title':'Show','ids':{'tmdb':80748}},
                     'episode':{'title':'Episode','season':season,'number':episode,'ids':{'tmdb':999}},
                     'first_aired':'2026-11-02T12:00:00Z'}
            item=env['FactoryCalendarEpisodeItemMapper'](copy.deepcopy(fixture)).item
            query=parse_qs(urlencode(dict(item['params'],info='play')))
            self.assertEqual(query['season'],[str(season)])
            self.assertEqual(query['episode'],[str(episode)])
            self.assertEqual(query['tmdb_id'],['80748'])
            self.assertEqual(item['infolabels']['season'],season)
        movie=env['FactoryCalendarMovieItemMapper']({'movie':{'title':'Movie','ids':{'tmdb':42},'released':'2026-11-02'}}).item
        self.assertNotIn('season',movie['params'])
        self.assertNotIn('episode',movie['params'])

    def test_reauthorize_uses_actual_lifetime_instead_of_fixed_90_days(self):
        rel='addons/skin.xenon2/resources/scripts/reauthorize/main/lib/reauthorize/modules/sync/trakt_sync.py'
        tree=ast.parse((self.engine.root/rel).read_text())
        helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_trakt_lifetime')
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='trakt_data' for t in n.targets))
        env={'json':json}
        exec(compile(ast.Module(body=[helper],type_ignores=[]),'lifetime','exec'),env)
        for lifetime in (21600,86400,604800,7776000):
            from types import SimpleNamespace
            value=env['_trakt_lifetime'](SimpleNamespace(getSetting=lambda key:str(lifetime)))
            self.assertEqual(value,lifetime)
            env.update(your_token='synthetic-access',your_refresh='synthetic-refresh',your_expires='2000000000.0',your_lifetime=value)
            exec(compile(ast.Module(body=[assignment],type_ignores=[]),'metadata-adapter','exec'),env)
            data=json.loads(env['trakt_data'])
            self.assertEqual(data['created_at']+data['expires_in'],2000000000)
            self.assertEqual(data['expires_in'],lifetime)
            self.assertEqual(data['access_token'],'synthetic-access')
            self.assertEqual(data['refresh_token'],'synthetic-refresh')


if __name__=='__main__':unittest.main()
