"""A narrowly checked, reversible integration at the player-selection layer."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from jamieplayerorder.order import atomic_write


RELATIVE = 'resources/tmdbhelper/lib/player/dialog/standard.py'
ORIGINAL = '''    def select_player(players_list, header=None, detailed=True, index=False):
        """ Select from a list of players """
        x = Dialog().select(
            header or get_localized(32042),
            [i.listitem for i in players_list],
            useDetails=detailed
        )
        return x if index or x == -1 else players_list[x].posx
'''
HOOK = '''        # JAMIE_PLAYER_ORDER_BEGIN
        try:
            import os
            import sys
            import xbmc
            from xbmcaddon import Addon
            if xbmc.getCondVisibility('System.AddonIsEnabled(script.jamied91.playerorder)'):
                organiser = Addon('script.jamied91.playerorder')
                library = os.path.join(organiser.getAddonInfo('path'), 'resources', 'lib')
                if library not in sys.path:
                    sys.path.insert(0, library)
                from jamieplayerorder.chooser import select_player
                x = select_player(players_list, header or get_localized(32042), detailed)
                return x if index or x == -1 else players_list[x].posx
        except Exception as error:
            import xbmc
            xbmc.log('Jamie Player Menu Organiser: original chooser used ('+type(error).__name__+')', xbmc.LOGERROR)
            Dialog().notification('Player Menu Organiser', 'Original chooser used; see Kodi log', time=4000)
        # JAMIE_PLAYER_ORDER_END
'''
PATCHED = ORIGINAL.replace('        x = Dialog().select(', HOOK+'        x = Dialog().select(', 1)


class Unsupported(ValueError):
    pass


def function(source):
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PlayerSelectStandard']
    if len(classes) != 1:
        raise Unsupported('The player chooser class has changed; no files were changed.')
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == 'select_player']
    if len(methods) != 1 or [ast.dump(d) for d in methods[0].decorator_list] != [ast.dump(ast.Name(id='staticmethod', ctx=ast.Load()))]:
        raise Unsupported('The player chooser interface has changed; no files were changed.')
    return methods[0]


def signature(node):
    node = ast.parse(ast.unparse(node)).body[0]
    node.decorator_list = []
    return ast.dump(node, include_attributes=False)


def expected(text):
    import textwrap
    return signature(ast.parse(textwrap.dedent(text)).body[0])


def inspect(source):
    node = function(source)
    actual = signature(node)
    if actual == expected(ORIGINAL):
        return 'ready'
    if actual == expected(PATCHED):
        return 'enabled'
    raise Unsupported('The player chooser code has changed; no files were changed.')


def transform(raw, enable):
    bom = raw.startswith(b'\xef\xbb\xbf')
    source = raw.decode('utf-8-sig')
    state = inspect(source)
    if state == ('enabled' if enable else 'ready'):
        return raw
    node = function(source)
    lines = source.splitlines(keepends=True)
    ending = '\r\n' if '\r\n' in source else '\n'
    replacement = (PATCHED if enable else ORIGINAL).replace('\n', ending)
    changed = ''.join(lines[:node.lineno-1])+replacement+''.join(lines[node.end_lineno:])
    compile(changed, RELATIVE, 'exec')
    inspect(changed)
    return (b'\xef\xbb\xbf' if bom else b'')+changed.encode('utf-8')


def apply(helper, profile, enable=True):
    helper, profile = Path(helper), Path(profile)
    target = helper/RELATIVE
    before = target.read_bytes()
    after = transform(before, enable)
    if after == before:
        return False
    # No database, media, account settings or player definitions are changed.
    folder = profile/'backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    (folder/'standard.py').write_bytes(before)
    (folder/'manifest.json').write_text(json.dumps({
        'operation': 'enable' if enable else 'disable',
        'before_sha256': hashlib.sha256(before).hexdigest(),
        'after_sha256': hashlib.sha256(after).hexdigest()}, indent=2))
    if target.read_bytes() != before:
        raise Unsupported('The chooser changed during backup; no files were changed.')
    atomic_write(target, after)
    if target.read_bytes() != after:
        raise OSError('Chooser write verification failed.')
    return True
