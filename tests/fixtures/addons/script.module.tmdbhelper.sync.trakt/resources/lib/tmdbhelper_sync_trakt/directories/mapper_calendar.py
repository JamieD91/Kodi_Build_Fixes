from jurialmunkey.ftools import cached_property
from tmdbhelper.lib.addon.tmdate import convert_timestamp
from tmdbhelper_sync_trakt.directories.mapper_calendar_base import CalendarItemMapper
from tmdbhelper.lib.items.directories.mapper_standard import MovieItemMapper


class CalendarMovieItemMapper(CalendarItemMapper, MovieItemMapper):
    tmdb_type = 'movie'
    mediatype = 'movie'

    @cached_property
    def air_date(self):
        try:
            return convert_timestamp(self.meta['released'], utc_convert=True, time_fmt="%Y-%m-%d", time_lim=10)
        except (KeyError, TypeError, AttributeError):
            return


def FactoryCalendarMovieItemMapper(meta, add_infoproperties=None):
    return CalendarMovieItemMapper(meta, add_infoproperties, sub_type='movie')
