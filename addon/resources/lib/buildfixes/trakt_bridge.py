"""ReAuthorize compatibility with the extracted Trakt provider's credentials.

Only configure a known application key when provider tokens already belong to
ReAuthorize. User tokens and application keys are obtained locally, never shipped.
"""
import ast
import json
import re
import textwrap
from buildfixes.engine import FileChange, Incompatible, fingerprint, safe_path

PROVIDER = 'script.module.tmdbhelper.sync.trakt'
BASE = 'addons/'+PROVIDER+'/resources/lib/tmdbhelper_sync_trakt/'
KEYS = BASE+'api/keys.py'
SYNC = 'addons/skin.xenon2/resources/scripts/reauthorize/main/lib/reauthorize/modules/sync/trakt_sync.py'
VARIABLES = 'addons/skin.xenon2/resources/scripts/reauthorize/core/resources/libs/common/var.py'
LEGACY_ROUTE = "path_tmdbh = addons + translatePath('plugin.video.themoviedb.helper/resources/tmdbhelper/lib/api/api_keys/trakt.py')\n"
PROVIDER_ROUTE = LEGACY_ROUTE+"""_tmdbh_provider_keys = addons + translatePath('script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/api/keys.py')
if xbmcvfs.exists(_tmdbh_provider_keys):
    path_tmdbh = _tmdbh_provider_keys
"""

LEGACY = '''if 'TMDb Helper' in current:
    if xbmcvfs.exists(var.chk_tmdbh) and (not xbmcvfs.exists(var.tmdbh_ud)):
        os.mkdir(var.tmdbh_ud)
        xbmcvfs.copy(os.path.join(var.tmdbh), os.path.join(var.chkset_tmdbh))
    if xbmcvfs.exists(var.chk_tmdbh) and (not xbmcvfs.exists(var.chkset_tmdbh)):
        xbmcvfs.copy(os.path.join(var.tmdbh), os.path.join(var.chkset_tmdbh))
    if xbmcvfs.exists(var.chk_tmdbh):
        chk_auth_tmdbh = xbmcaddon.Addon('plugin.video.themoviedb.helper').getSetting('trakt_token')
        if False:
            pass
        else:
            with open(var.path_tmdbh, 'r') as f:
                data = f.read()
            client = data.replace(var.tmdbh_client, var.client_am).replace(var.tmdbh_secret, var.secret_am)
            with open(var.path_tmdbh, 'w') as f:
                f.write(client)
            addon = xbmcaddon.Addon('plugin.video.themoviedb.helper')
            your_expires_float = float(your_expires)
            your_expires_rnd = int(your_expires_float)
            import json
            lifetime = int(float(accountmgr.getSetting('trakt.lifetime') or 604800))
            tmdbh_data = json.dumps({'access_token': your_token, 'refresh_token': your_refresh, 'token_type': 'bearer', 'expires_in': lifetime, 'created_at': your_expires_rnd - lifetime, 'scope': 'public'})
            addon.setSettingString('trakt_token', tmdbh_data)
            addon.setSetting('startup_notifications', 'false')
'''

REPAIRED = r'''if 'TMDb Helper' in current:
    if xbmcvfs.exists(var.chk_tmdbh):
        import ast
        import json
        import tempfile
        provider_id = 'script.module.tmdbhelper.sync.trakt'
        key_path = os.path.join(var.addons, provider_id, 'resources', 'lib', 'tmdbhelper_sync_trakt', 'api', 'keys.py')
        provider_present = xbmcvfs.exists(key_path)
        changed = False
        if provider_present:
            with open(key_path, 'rb') as source:
                key_bytes = source.read()
            key_text = key_bytes.decode('utf-8-sig')
            tree = ast.parse(key_text)
            statements = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
            if len(statements) != 1 or not isinstance(statements[0], ast.Assign) or len(statements[0].targets) != 1 or not isinstance(statements[0].targets[0], ast.Name) or statements[0].targets[0].id != 'CLIENT_ID' or not isinstance(statements[0].value, ast.Constant) or not isinstance(statements[0].value.value, str):
                raise RuntimeError('TMDb Helper provider key layout changed; credentials not overwritten')
            current_key = statements[0].value.value
            if current_key not in (var.tmdbh_client, var.client_am):
                raise RuntimeError('Custom TMDb Helper provider application key; credentials not overwritten')
            if current_key != var.client_am:
                # One recognised configuration literal, not provider behavior.
                literal = statements[0].value
                lines = key_text.splitlines(keepends=True)
                if literal.lineno != literal.end_lineno:
                    raise RuntimeError('TMDb Helper provider key layout changed')
                line = lines[literal.lineno-1].encode('utf-8')
                lines[literal.lineno-1] = (line[:literal.col_offset]+repr(var.client_am).encode('utf-8')+line[literal.end_col_offset:]).decode('utf-8')
                new_key = (b'\xef\xbb\xbf' if key_bytes.startswith(b'\xef\xbb\xbf') else b'')+''.join(lines).encode('utf-8')
                fd, temporary = tempfile.mkstemp(prefix='keys.reauthorize-', dir=os.path.dirname(key_path))
                try:
                    with os.fdopen(fd, 'wb') as source:
                        source.write(new_key)
                        source.flush()
                        os.fsync(source.fileno())
                    os.replace(temporary, key_path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                changed = True
        elif xbmcvfs.exists(var.path_tmdbh):
            with open(var.path_tmdbh, 'r') as source:
                data = source.read()
            client = data.replace(var.tmdbh_client, var.client_am).replace(var.tmdbh_secret, var.secret_am)
            if client != data:
                with open(var.path_tmdbh, 'w') as source:
                    source.write(client)
                changed = True
        else:
            raise RuntimeError('TMDb Helper application key location changed; credentials not overwritten')
        lifetime = _trakt_lifetime(accountmgr)
        tmdbh_data = json.dumps({'access_token': your_token, 'refresh_token': your_refresh,
            'token_type': 'bearer', 'expires_in': lifetime,
            'created_at': int(float(your_expires))-lifetime, 'scope': 'public'})
        targets = [(xbmcaddon.Addon('plugin.video.themoviedb.helper'), 'trakt_token')]
        if provider_present:
            targets.append((xbmcaddon.Addon(provider_id), 'token'))
        for target, name in targets:
            if target.getSetting(name) != tmdbh_data:
                target.setSetting(name, tmdbh_data)
                if target.getSetting(name) != tmdbh_data:
                    raise RuntimeError('TMDb Helper credential write failed')
                changed = True
        xbmcaddon.Addon('plugin.video.themoviedb.helper').setSetting('startup_notifications', 'false')
        if changed:
            home = xbmcgui.Window(10000)
            for name in ('TraktUserToken', 'TraktIsAuth', 'TraktRefreshAttempts', 'TraktRefreshTimeStamp', 'TraktAttemptedLogin'):
                home.clearProperty('TMDbHelper.'+name)
'''


def literal_settings(raw):
    tree = ast.parse(raw.decode('utf-8-sig'))
    functions = {n.name:n for n in tree.body if isinstance(n, ast.FunctionDef)}
    function = functions.get('traktID')
    if function is None or len(function.body) != 1 or not isinstance(function.body[0], ast.Return):
        raise Incompatible('ReAuthorize application-key function changed')
    master = ast.literal_eval(function.body[0].value)
    original = next((ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='tmdbh_client' for t in n.targets)), None)
    if not all(isinstance(key,str) and re.fullmatch('[a-fA-F0-9]{64}',key) for key in (master, original)):
        raise Incompatible('ReAuthorize application-key configuration changed')
    return master, original


def patch_keys(raw, master, original):
    text = raw.decode('utf-8-sig')
    nodes = [n for n in ast.parse(text).body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str))]
    if len(nodes)!=1 or not isinstance(nodes[0],ast.Assign) or len(nodes[0].targets)!=1 or not isinstance(nodes[0].targets[0],ast.Name) or nodes[0].targets[0].id!='CLIENT_ID':
        raise Incompatible('Provider application-key module changed')
    node = nodes[0].value
    if not isinstance(node,ast.Constant) or node.value not in (master, original) or node.lineno!=node.end_lineno:
        raise Incompatible('Unknown or custom provider application key; left unchanged')
    if node.value == master:
        return raw
    lines = text.splitlines(keepends=True)
    line = lines[node.lineno-1].encode('utf-8')
    lines[node.lineno-1] = (line[:node.col_offset]+repr(master).encode()+line[node.end_col_offset:]).decode('utf-8')
    return (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')+''.join(lines).encode('utf-8')


def patch_sync(raw):
    text = raw.decode('utf-8-sig')
    candidates = [n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.If)
        and fingerprint(n.test)==fingerprint(ast.parse(LEGACY).body[0].test)]
    if len(candidates)!=1:
        raise Incompatible('ReAuthorize TMDb Helper sync branch changed')
    node = candidates[0]
    if fingerprint(node)==fingerprint(ast.parse(REPAIRED).body[0]):
        return raw
    if fingerprint(node)!=fingerprint(ast.parse(LEGACY).body[0]):
        raise Incompatible('ReAuthorize TMDb Helper sync implementation changed')
    ending = '\r\n' if '\r\n' in text else '\n'
    replacement = textwrap.indent(REPAIRED.rstrip('\n'), ' '*node.col_offset).replace('\n',ending)+ending
    lines = text.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1])+replacement+''.join(lines[node.end_lineno:])
    compile(result, SYNC, 'exec')
    return (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')+result.encode('utf-8')


def patch_route(raw):
    from buildfixes.engine import patch_python
    return patch_python(raw,[{'kind':'block','scope':[],
        'before':[LEGACY_ROUTE],'after':PROVIDER_ROUTE}])


def inspect_bridge(engine, plan):
    if not safe_path(engine.root,KEYS).is_file():
        return  # Legacy core layout uses its original adapter.
    if not safe_path(engine.root,VARIABLES).is_file():
        return  # ReAuthorize isn't embedded in this skin.
    master, original = literal_settings(engine.read(VARIABLES))
    api = ast.parse(engine.read(BASE+'api/api.py'))
    if not any(isinstance(n,ast.ImportFrom) and n.module=='tmdbhelper_sync_trakt.api.keys'
               and any(a.name=='CLIENT_ID' and a.asname is None for a in n.names) for n in api.body):
        raise Incompatible('Provider application-key import changed')
    cls = next((n for n in api.body if isinstance(n,ast.ClassDef) and n.name=='TraktAPI'),None)
    if cls is None or not any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='client_id' for t in n.targets)
                             and isinstance(n.value,ast.Name) and n.value.id=='CLIENT_ID' for n in cls.body):
        raise Incompatible('Provider API application-key contract changed')
    from buildfixes.special import get_settings
    account = get_settings(engine,'script.module.reauthorize')
    token = json.loads(engine.backend.get_setting(PROVIDER,'token') or '{}')
    if not account.get('trakt.token') or not account.get('trakt.refresh'):
        return
    if token.get('access_token')!=account['trakt.token'] or token.get('refresh_token')!=account['trakt.refresh']:
        raise Incompatible('Provider has a separate sign-in; credentials left unchanged')
    plan.versions[PROVIDER] = engine.addon_version(PROVIDER)
    for path, transform in ((KEYS,lambda raw:patch_keys(raw,master,original)), (SYNC,patch_sync),
                            (VARIABLES,patch_route)):
        prior = next((change for change in plan.files if change.path==path),None)
        before = prior.after if prior else engine.read(path)
        after = transform(before)
        if before != after:
            if prior:
                prior.after = after
            else:
                plan.files.append(FileChange(path,before,after))
