"""Playback IDs stay unambiguous without inventing Trakt-specific identifiers."""
import ast
import json
import tempfile
import unittest
from types import SimpleNamespace as NS
from support import make_engine


SOURCE = 'addons/plugin.video.redlight/resources/lib/modules/player.py'


class RedLightScrobbleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine = make_engine(self.temp.name)

    def test_runtime_preserves_show_and_movie_ids_and_other_playback_properties(self):
        plan=self.engine.scan(['redlight_scrobble'])[0]
        self.assertEqual(plan.status,'ready');self.engine.apply(plan)
        namespace={'json':json}
        # This fixture has the same real property-writing method and no Kodi imports.
        exec(compile(self.engine.read(SOURCE),'redlight-test','exec'),namespace)
        for title,media,tmdb,imdb,tvdb in [
                ('CIA','episode',289560,'tt35515227',462856),
                ('Star Trek: The Next Generation','episode',655,'tt0092455',71470),
                ('Same title, another release','movie',123,'tt0123456',None)]:
            written={};namespace['ku']=NS(set_property=lambda k,v:written.__setitem__(k,v))
            player=namespace['RedLightPlayer']()
            player.title=title;player.media_type=media
            player.tmdb_id=tmdb;player.imdb_id=imdb;player.tvdb_id=tvdb
            player.set_playback_properties()
            expected={'tmdb':tmdb,'imdb':imdb}
            if media=='episode':expected['tvdb']=tvdb
            self.assertEqual(json.loads(written['script.trakt.ids']),expected)
            self.assertEqual(written['unrelated.playback'],title)
        self.assertEqual(self.engine.scan(['redlight_scrobble'])[0].status,'fixed')

    def test_unknown_identifier_mapping_is_left_unchanged(self):
        path=self.engine.root/SOURCE
        raw=path.read_bytes().replace(b'make_trakt_slug(self.title)',b'self.trakt_slug')
        path.write_bytes(raw)
        plan=self.engine.scan(['redlight_scrobble'])[0]
        self.assertEqual(plan.status,'incompatible')
        self.assertEqual(path.read_bytes(),raw)
