class RedLightPlayer:
    def set_playback_properties(self):
        try:
            trakt_ids = {'tmdb': self.tmdb_id, 'imdb': self.imdb_id, 'slug': make_trakt_slug(self.title)}
            if self.media_type == 'episode': trakt_ids['tvdb'] = self.tvdb_id
            ku.set_property('script.trakt.ids', json.dumps(trakt_ids))
            ku.set_property('unrelated.playback', self.title)
        except: pass
