"""Regression cases exercising actual repaired add-on methods with Kodi stubs."""
from pathlib import Path
from types import SimpleNamespace as NS, SimpleNamespace as N, ModuleType
from functools import cached_property
from unittest.mock import patch
import ast
import json
import os
import sys

STAGED = None
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
passed = []

def prepared(name):
    return (STAGED / name).read_text(encoding='utf-8')

def functions(rel, names, env):
    tree = ast.parse((STAGED / rel).read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), rel, 'exec'), env)
    return [env[n] for n in names]


def tmdb_player():
    env = {}
    wrapper, inner = functions('plugin.video.themoviedb.helper/resources/tmdbhelper/lib/monitor/player.py',
                               ('get_playingitem', '_get_playingitem'), env)
    class Harness:
        get_playingitem = wrapper
        _get_playingitem = inner
        def __init__(self):
            self.playing = False
            self.resets = 0
            self.file_calls = 0
        def isPlaying(self): return self.playing
        def reset_properties(self): self.resets += 1
        def getPlayingFile(self):
            self.file_calls += 1
            if not self.playing: raise RuntimeError('Kodi is not playing any file')
            return self.file
    p = Harness()
    p.get_playingitem()
    assert p.resets == 1
    p.playing, p.file = True, '/test/dummy.mp4'
    p.get_playingitem()
    assert p.resets == 2 and p.file_calls == 1
    p.file = '/test/episode.mkv'
    p.reset_player_item = lambda: setattr(p, 'playing', False) or (_ for _ in ()).throw(RuntimeError('stopped during metadata'))
    p.get_playingitem()
    assert p.resets == 3
    p.playing = True
    p.reset_player_item = lambda: (_ for _ in ()).throw(RuntimeError('real failure while playing'))
    try: p.get_playingitem()
    except RuntimeError: pass
    else: raise AssertionError('Live playback errors must not be swallowed')
    p.reset_player_item = lambda: None
    p.dbtype, p.dbid, p.imdb_id, p.query, p.year, p.season, p.episode = 'episode', 1, 'tt1', 'show', None, 1, 2
    p.previous_item = ('episode', 1, 'tt1', 'show', None, 1, 2)
    p.get_playingitem()
    assert p.current_item == p.previous_item
    passed.append('TMDb Helper: stopped/dummy/mid-callback/live-error/normal item cases')


def autowidget():
    threads, history, queued = [], [], []
    class Thread:
        def __init__(self, target): self.target = target
        def start(self): threads.append(self.target)
    class Native:
        playing = True
        total = 100
        now = 25
        def getPlayingFile(self):
            if not self.playing: raise RuntimeError('stopped')
            return '/test/episode.mkv'
        def getTotalTime(self):
            if not self.playing: raise RuntimeError('stopped')
            return self.total
        def getTime(self):
            if not self.playing: raise RuntimeError('stopped')
            return self.now
        def isPlaying(self): return self.playing
    tree = ast.parse((STAGED / 'plugin.program.autowidget/resources/lib/refresh.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Player')
    env = {'xbmc': NS(Player=Native), 'utils': NS(log=lambda *a: None),
           'cache': NS(save_playback_history=lambda *a: history.append(a),
                       widgets_changed_by_watching=lambda t: [('hash', '/widget')],
                       push_cache_queue=lambda p: queued.append(p)),
           'threading': NS(Thread=Thread)}
    p = None
    env['time'] = NS(sleep=lambda n: setattr(p, 'playing', False))
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'autowidget-player', 'exec'), env)
    p = env['Player']()
    p.playing_type = lambda: 'episode'
    p.onPlayBackStarted()
    p.onPlayBackEnded()
    assert not history and not queued
    p.onPlayBackStarted()
    p.onAVStarted()
    threads[-1]()
    p.onPlayBackEnded()
    assert history == [('episode', 25, '/test/episode.mkv')] and queued == ['/widget']
    p.onPlayBackEnded()
    assert len(history) == 1
    p.playing = True
    p.onPlayBackStarted()
    p.onAVStarted()
    stale_thread = threads[-1]
    p.onPlayBackStarted()
    stale_thread()
    assert p.playingTime == 0 and p.path is None
    p.onAVStarted()
    p.onPlayBackSeek(25000, 1000)
    assert p.playingTime == 25
    p.totalTime = -1
    p.onPlayBackEnded()
    assert len(history) == 1
    p.playing = False
    p.onPlayBackStarted()
    p.onAVStarted()
    p.onPlayBackEnded()
    assert len(history) == 1
    passed.append('AutoWidget: pre-AV stop, valid playback, duplicate stop, stale thread, seek and unavailable duration')


def widget_queue():
    reads = []
    data = {os.path.join('root', 'bad.history'): {'path': ''},
            os.path.join('root', 'good.history'): {'path': '/widget'}}
    env = {'_addon_data': 'root', '_startup_time': 0, '_playback_history_path': 'plays',
           'os': os, 'random': NS(random=lambda: 1), 'path2hash': lambda p: 'hash',
           'utils': NS(read_json=lambda p, default=None: reads.append(p) or data.get(p, {}), log=lambda *a: None),
           'xbmcvfs': NS(listdir=lambda p: ([], ['bad.history', 'good.history']), Stat=lambda p: NS(st_mtime=lambda: 10)),
           'chance_playback_updates_widget': lambda *a: 0.9}
    push, changed = functions('plugin.program.autowidget/resources/lib/common/cache.py',
                              ('push_cache_queue', 'widgets_changed_by_watching'), env)
    push(None)
    push('')
    assert not reads
    assert list(changed('episode')) == [('hash', '/widget')]
    passed.append('AutoWidget queue: empty submissions and old empty history excluded; valid widget retained')


def umbrella():
    errors, summaries, lookups = [], [], []
    for name in ('resources', 'resources.lib', 'resources.lib.modules'):
        sys.modules[name] = ModuleType(name)
    log = ModuleType('resources.lib.modules.log_utils')
    log.error = lambda *a: errors.append(a)
    log.log = lambda *a: None
    log.LOGINFO = 1
    sys.modules['resources.lib.modules.log_utils'] = log
    sys.modules['resources.lib.modules'].log_utils = log
    class Thread:
        def __init__(self, target, args): self.target, self.args = target, args
        def start(self): self.target(*self.args)
        def join(self): pass
    scenario = {}
    class API:
        def IdLookup(self, imdb, tvdb): return {'id': '77'}
        def get_showSeasons_meta(self, tmdb):
            lookups.append(tmdb)
            assert tmdb == '77'
            return {'seasons': scenario['seasons'], 'status': 'ended', 'plot': 'show plot'}
        def get_seasonEpisodes_meta_checked(self, tmdb, season):
            assert (season, scenario['episode']) == scenario['expected']
            return {'episodes': [{'episode': scenario['episode'], 'plot': 'episode plot', 'premiered': '2000-01-01', 'duration': 40}]}
    def all_pages(url):
        return [{'seasons': [{'number': scenario['watched_season'], 'episodes': [{'number': scenario['watched_episode']}]}],
                 'show': {'title': 'Test', 'status': 'ended', 'aired_episodes': 999, 'runtime': 40,
                          'ids': {'trakt': scenario.get('trakt', 123), 'imdb': scenario.get('imdb', 'tt1'),
                                  'tmdb': scenario.get('tmdb', '77'), 'tvdb': '99999'}}}]
    env = {'trakt': NS(get_all_pages=all_pages, getTVShowSummary=lambda id, full: summaries.append(id) or {}),
           'tmdb_indexer': API, 'cache': NS(get=lambda fn, ttl, *a: fn(*a)), 'Thread': Thread,
           'getSetting': lambda key: '10' if key == 'dev.batch.size' else '',
           'control': NS(monitor=NS(abortRequested=lambda: False)), '__name__': 'umbrella-test'}
    method, = functions('plugin.video.umbrella/resources/lib/menus/episodes.py', ('trakt_progress_list',), env)
    def run(seasons, watched, expected=None, **ids):
        scenario.clear()
        scenario.update(seasons=seasons, watched_season=watched[0], watched_episode=watched[1],
                        expected=expected, episode=expected[1] if expected else None, **ids)
        target = NS(list=[], showspecials=False, enable_fanarttv=False, today_date='2026-11-02')
        result = method(target, '/test', 'test', 'en', direct=True)
        assert len(result) == (1 if expected else 0), result
        assert not errors, errors
        return result
    run([], (1, 1))
    run(None, (1, 1))
    run([{'season_number': 1, 'episode_count': 4}], (1, 1), (1, 2))
    run([{'season_number': 2, 'episode_count': 8}, {'season_number': 1, 'episode_count': 4}], (2, 5), (2, 6))
    run([{'season_number': 3, 'episode_count': 4}, {'season_number': 0, 'episode_count': 2}, {'season_number': 1, 'episode_count': 2}], (1, 2), (3, 1))
    run([{'season_number': 1, 'episode_count': 4}], (1, 1), (1, 2), tmdb=None)
    assert lookups[-1] == '77' and summaries[-1] == 123
    before = len(summaries)
    run([], (1, 1), trakt=None, imdb=None)
    assert len(summaries) == before, 'TVDB IDs must not be sent as Trakt show IDs'
    passed.append('Umbrella: empty seasons, unordered seasons, season gaps, normal next episode, recovered TMDb ID, correct Trakt ID')


def code(raw,ns):
    tree=ast.parse(raw)
    tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
    exec(compile(tree,'isolated-addon','exec'),ns)


def context():
    tree=ast.parse(prepared('script.trakt/resources/lib/script.py'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_route_nonlibrary_context')
    labels={};commands=[];dialogs=[];kind=['episode']
    gui=ModuleType('xbmcgui');gui.Dialog=lambda:N(ok=lambda *args:dialogs.append(args))
    ns={'xbmc':N(getInfoLabel=lambda name:labels.get(name,''),executebuiltin=commands.append),'kodiUtilities':N(getMediaType=lambda:kind[0])}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'context','exec'),ns)
    route=ns['_route_nonlibrary_context']
    with patch.dict(sys.modules,{'xbmcgui':gui}):
        labels['ListItem.DBID']='123';assert route() is False;assert not commands
        labels.clear();labels['ListItem.FileNameAndPath']='plugin://plugin.video.themoviedb.helper/?info=details&tmdb_type=tv&tmdb_id=80748&season=2&episode=4'
        assert route() is True;assert commands.pop()=='RunScript(plugin.video.themoviedb.helper,sync_options,tmdb_type=tv,tmdb_id=80748,season=2,episode=4)'
        labels['ListItem.FileNameAndPath']='plugin://plugin.video.themoviedb.helper/?info=details&tmdb_type=tv&tmdb_id=80748'
        labels['ListItem.Season']='0';labels['ListItem.Episode']='1'
        assert route() is True;assert commands.pop().endswith('season=0,episode=1)')
        labels.pop('ListItem.Episode');assert route() is True;assert dialogs;assert not commands
        kind[0]='show';assert route() is True;assert commands.pop().endswith('tmdb_id=80748)')
        labels['ListItem.FileNameAndPath']='plugin://plugin.video.themoviedb.helper/?tmdb_type=tv&tmdb_id=1%29%2CQuit%28'
        assert route() is True;assert not commands
        kind[0]='unknown';assert route() is False
    print('Trakt context: library path preserved; add-on show/episode/special routing validated; incomplete and unsafe IDs refused')


def weather():
    outlook=json.loads((FIXTURES/'weather-outlook.json').read_text())
    values={};warnings=[]
    ns={'OUTLOOK':outlook,'xbmc':N(LOGWARNING=2,log=lambda *a:warnings.append(a),getLocalizedString=str),'set_property':lambda k,v:values.update({k:v}),'CONDITION':{},'convert_temp':lambda value,*args:str(value),'LONGDAY':{'Fri':1},'SHORTDAY':{'Fri':2},'MAXDAYS':6,'TEMPUNIT':' C'}
    code(prepared('weather.multi/lib/providers/yahoo.py'),ns)
    assert ns['_condition_name']('Mostly cloudy')=='Mostly Cloudy'
    assert ns['_condition_name']('  partly CLOUDY ')=='Partly Cloudy'
    assert ns['_outlook_code']('Mostly cloudy Night')=='27'
    assert ns['_outlook_code']('Rain and snow')=='18'
    assert ns['_outlook_code']('Unfamiliar weather')=='44';assert len(warnings)==1
    for word,expected in [('Mostly cloudy','28'),('Partly sunny','30'),('Sunny','32')]:
        values.clear()
        ns['Weather'].get_daily_weather({'forecasts':[{'date':'Fri 30','highTemperature':70,'lowTemperature':50,'iconLabel':word}]})
        assert values['Day0.OutlookIcon']==expected+'.png',values
        assert values['Daily.1.FanartCode']==expected
    print('Weather: real daily forecast handles case changes and partly sunny; day/night mappings and unknown-condition fallback checked')


def scraper():
    raw=prepared('script.module.viperscrapers/lib/viperscrapers/sources_viperscrapers/torrents/torrentio.py')
    node=next(n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name=='source')
    payload=['{"streams": null}']
    ns={'jsloads':json.loads,'re':__import__('re'),'client':N(request=lambda *a,**kw:payload[0]),'cache':N(get=lambda *a:None),'source_utils':N(get_undesirables=lambda:None,check_foreign_audio=lambda:False),'getSetting':lambda *_:'false','homeWindow':N(setProperty=lambda *a:None,clearProperty=lambda *a:None,getProperty=lambda *_:''),'sleep':lambda *_:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'scraper','exec'),ns)
    provider=ns['source']();provider.get_pack_files=False
    assert provider._get_files('fixture')==[]
    payload[0]='{"streams": [{"infoHash":"fixture"}]}'
    assert provider._get_files('fixture')==[{'infoHash':'fixture'}]
    assert provider.sources({'aliases':[],'year':2026,'imdb':'tt1','tvshowtitle':'Fixture','title':'Episode','season':1,'episode':2},{})==[]
    assert provider.sources_packs({'aliases':[],'year':2026,'imdb':'tt1','tvshowtitle':'Fixture','title':'Episode','season':1,'episode':2},{})==[]
    print('Scraper: null API response and missing cached result return no sources; valid results preserved; no network requests made')


def scheduler_checks():
    rel='plugin.video.playlistbrowser/dlhd/exporter.py'
    tree=ast.parse((STAGED/rel).read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Scheduler')
    state={}
    flags={'exists':False,'auto':'true','now':1000,'runs':0}
    def save(addon,value): state.clear();state.update(value)
    def refresh(*args):
        flags['runs']+=1
        raise ValueError('simulated provider outage')
    class Thread:
        def __init__(self,target,daemon):self.target=target
        def start(self):self.target()
        def is_alive(self):return False
    env={'output_path':lambda a:'/playlist', 'read_state':lambda a:dict(state), 'save_state':save,
         'xbmcvfs':NS(exists=lambda p:flags['exists'], Stat=lambda p:NS(st_mtime=lambda:1000)),
         'time':NS(time=lambda:flags['now']), 'threading':NS(Thread=Thread), 'refresh':refresh,
         'LOG':NS(info=lambda *a:None,warning=lambda *a:None), 'reason':lambda e:'simulated outage',
         'repair_logo_paths':lambda *a:True}
    exec(compile(ast.Module(body=[cls],type_ignores=[]),rel,'exec'),env)
    addon=NS(getSetting=lambda k:flags['auto'] if k=='DLHD_auto_refresh' else '48')
    scheduler=env['Scheduler'](addon,'local')
    scheduler.tick(1000)
    assert flags['runs']==1 and state['retry_after']==4600
    flags['now']=1060;scheduler.tick(1060)
    assert flags['runs']==1, 'Missing M3U must not bypass one-hour retry backoff'
    flags['now']=4600;scheduler.tick(4600)
    assert flags['runs']==2
    state.clear();flags['auto']='false';flags['exists']=False
    assert scheduler.due(5000), 'First-time initialization still runs with periodic updates off'
    flags['exists']=True
    assert not scheduler.due(5000)
    flags['auto']='true';state.update(path='/playlist',last_success=1000)
    assert not scheduler.due(2000)
    assert scheduler.due(1000+48*3600)
    state.update(path='/old-folder',retry_after=99999999);flags['exists']=False
    assert scheduler.due(5000), 'Changing the output location must not inherit the old retry timer'
    return 'Guide scheduler: failed initialization backs off, retry deadline works, normal 48h schedule and manual initialization preserved'

