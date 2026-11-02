"""Player identity and small atomic preferences; no playback or Kodi imports."""
import json
import os
from pathlib import Path
import tempfile


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class OrderStore:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'schema': 1, 'scopes': {}}
        if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('scopes'), dict):
            raise ValueError('Unsupported saved player order; preferences have been preserved.')
        for key, values in data['scopes'].items():
            if not isinstance(key, str) or not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError('Invalid saved player order; preferences have been preserved.')
            if len(values) != len(set(values)):
                raise ValueError('Duplicate saved player identities; preferences have been preserved.')
        return data

    def ordered(self, scope, rows):
        order = self.read()['scopes'].get(scope, [])
        ranks = {key: n for n, key in enumerate(order)}
        return sorted(rows, key=lambda row: ranks.get(row.key, len(order)))

    def save_visible(self, scope, keys):
        """Reorder visible entries while keeping temporarily absent identities."""
        if len(keys) != len(set(keys)):
            raise ValueError('Ambiguous player identities; no order was saved.')
        data = self.read()
        old = data['scopes'].get(scope, [])
        full = old + [key for key in keys if key not in old]
        visible = set(keys)
        positions = [n for n, key in enumerate(full) if key in visible]
        for n, key in zip(positions, keys):
            full[n] = key
        data['scopes'][scope] = full
        atomic_write(self.path, (json.dumps(data, indent=2)+'\n').encode())

    def reset(self, scope=None):
        data = self.read()
        if scope is None:
            data['scopes'] = {}
        else:
            data['scopes'].pop(scope, None)
        atomic_write(self.path, (json.dumps(data, indent=2)+'\n').encode())

    def red_light_first(self):
        data = self.read()
        old = data['scopes'].get('providers', [])
        red = 'plugin.video.redlight'
        data['scopes']['providers'] = [red] + [key for key in old if key != red]
        atomic_write(self.path, (json.dumps(data, indent=2)+'\n').encode())


class Row:
    def __init__(self, item, index, grouped):
        self.item = item
        self.index = index
        self.plugin = item.player.plugin_name
        self.grouped = grouped
        self.key = str(item.uid) if grouped else json.dumps(
            [self.plugin, item.player.file, item.player.mode, str(item.uid)], separators=(',', ':'))

    @property
    def listitem(self):
        return self.item.listitem


def describe(items):
    # The caller is deliberately checked at enable time against both Helper
    # chooser implementations. Group identity differs from method identity.
    grouped = bool(items) and all(type(item).__name__ == 'PlayerListItemCombined' for item in items)
    rows = [Row(item, n, grouped) for n, item in enumerate(items)]
    plugins = {row.plugin for row in rows}
    scope = 'providers' if grouped else 'methods:'+next(iter(plugins)) if len(plugins) == 1 else 'players'
    if len({row.key for row in rows}) != len(rows):
        raise ValueError('Ambiguous player identities; the original chooser will be used.')
    return scope, rows
