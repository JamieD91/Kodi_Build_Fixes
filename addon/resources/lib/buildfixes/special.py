"""Repairs needing an XML, settings, or cross-module compatibility check."""
import ast
import json
import re
import xml.etree.ElementTree as ET
from buildfixes.engine import FileChange, SettingChange, Incompatible, safe_path, fingerprint


CORE = 'addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/items/directories/'
CALENDARS = (
    'addons/script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/mapper_calendar_base.py',
    CORE + 'mapper_calendar.py',
    CORE + 'trakt/mapper_calendar.py',
)


def get_settings(engine, addon):
    raw = engine.read('userdata/addon_data/' + addon + '/settings.xml')
    return {e.get('id'): e.text or e.get('value') or '' for e in ET.fromstring(raw).iter('setting')}


def calendar(engine, plan):
    from buildfixes import calendar_rules
    found = next((p for p in CALENDARS if safe_path(engine.root, p).is_file()), None)
    if not found:
        raise FileNotFoundError('Calendar mapper not installed')
    if 'script.module.tmdbhelper.sync.trakt/' in found:
        plan.versions['script.module.tmdbhelper.sync.trakt'] = engine.addon_version('script.module.tmdbhelper.sync.trakt')
        lists = engine.read('addons/script.module.tmdbhelper.sync.trakt/resources/lib/tmdbhelper_sync_trakt/directories/lists_calendar_base.py').decode('utf-8-sig')
        tree = ast.parse(lists)
        if not any(isinstance(n, ast.ImportFrom) and n.module == 'tmdbhelper_sync_trakt.directories.mapper_calendar_base'
                   and any(a.name == 'FactoryCalendarEpisodeItemMapper' and a.asname is None for a in n.names) for n in tree.body):
            raise Incompatible('Calendar mapper import changed')
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ListCalendarProperties'), None)
        if not cls or not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'item_mapper_class' for t in n.targets)
                              and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == 'staticmethod'
                              and len(n.value.args) == 1 and isinstance(n.value.args[0], ast.Name)
                              and n.value.args[0].id == 'FactoryCalendarEpisodeItemMapper' for n in cls.body):
            raise Incompatible('Calendar factory registration changed')
    else:
        expected='tmdbhelper.lib.items.directories.'+('trakt.mapper_calendar' if '/trakt/' in found else 'mapper_calendar')
        callers=(CORE+'lists_calendar.py',CORE+'trakt/lists_calendar.py')
        active=False
        for caller in callers:
            if not safe_path(engine.root,caller).is_file():continue
            tree=ast.parse(engine.read(caller))
            active=active or any(isinstance(n,ast.ImportFrom) and n.module==expected and
                any(a.name=='FactoryCalendarEpisodeItemMapper' and a.asname is None for a in n.names) for n in tree.body)
        if not active:raise Incompatible('Old calendar file is not referenced by a known caller')
    raw = engine.read(found)
    try:
        new = calendar_rules.patch_source(raw)
        if calendar_rules.validate_dependencies(engine.read(CORE + 'mapper_standard.py')):
            new = raw  # A known equivalent repair is already inherited upstream.
    except RuntimeError as error:
        raise Incompatible(str(error)) from error
    if new != raw:
        plan.files.append(FileChange(found, raw, new))
        plan.jobs.append('calendar_cache')


def extendedinfo(engine, plan):
    rel = 'addons/script.extendedinfo/addon.xml'
    raw = engine.read(rel)
    try:
        ET.fromstring(raw)
        return
    except ET.ParseError:
        pass
    text = raw.decode('utf-8-sig')
    pattern = r'^[ \t]*<menu\s+id=["\x27]kodi\.core\.main["\x27]\s*>\s*</menu>\s*</extension>[^\S\r\n]*(?:\r?\n)?'
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise Incompatible('Malformed manifest does not match the known orphan-menu defect')
    new = (text[:matches[0].start()] + text[matches[0].end():]).encode('utf-8')
    root = ET.fromstring(new)
    if root.get('id') != 'script.extendedinfo':
        raise Incompatible('Wrong manifest identity')
    if raw.startswith(b'\xef\xbb\xbf'):
        new = b'\xef\xbb\xbf' + new
    plan.files.append(FileChange(rel, raw, new))


def reauth(engine, plan):
    candidates = (
        'addons/skin.xenon2/resources/scripts/reauthorize/main/resources/settings.xml',
        'addons/script.module.reauthorize/resources/settings.xml',
    )
    rel = next((p for p in candidates if safe_path(engine.root, p).is_file()), None)
    if rel:
        raw = engine.read(rel)
        root = ET.fromstring(raw)
        nodes = [n for n in root.iter('setting') if 'traktAuth' in n.get('action', '')]
        if len(nodes) != 1:
            raise Incompatible('Trakt sign-in action changed')
        node = nodes[0]
        visible = node.get('visible')
        if visible and visible != 'eq(-6,)':
            raise Incompatible('Trakt sign-in visibility condition is unrecognised')
        if visible:
            before = [dict(n.attrib) for n in root.iter('setting')]
            node.attrib.pop('visible')
            node.set('label', 'Sign in / Reauthorize Trakt')
            new = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            # Only one resource-setting node changes; this is not account data.
            after = [dict(n.attrib) for n in ET.fromstring(new).iter('setting')]
            assert sum(a != b for a, b in zip(before, after)) == 1 and len(before) == len(after)
            plan.files.append(FileChange(rel, raw, new))
    try:
        master = get_settings(engine, 'script.module.reauthorize')
        value = engine.backend.get_setting('script.trakt', 'authorization')
        auth = json.loads(value or '{}')
        lifetime = int(float(master.get('trakt.lifetime') or '604800'))
        expiry = int(float(master.get('trakt.expires') or '0'))
        if lifetime <= 0:
            lifetime = 604800
        if expiry and auth.get('access_token') == master.get('trakt.token') and auth.get('refresh_token') == master.get('trakt.refresh'):
            if auth.get('created_at') != expiry - lifetime or auth.get('expires_in') != lifetime:
                plan.jobs.append('trakt_metadata')
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass  # No account needs metadata correction; the source fix still applies.


def liveguide(engine, plan):
    addon = 'plugin.video.playlistbrowser'
    stored = engine.backend.get_setting(addon, 'DLHD_base')
    current = stored.rstrip('/')
    target = engine.backend.provider_base().strip().rstrip('/')
    from urllib.parse import urlsplit
    parsed = urlsplit(target)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise Incompatible('Provider address must be a plain HTTPS origin')
    if current != target:
        if current not in ('', 'https://dlive.sx', 'https://thedaddy.pk'):
            raise Incompatible('A custom provider address is configured; it has been left alone')
        plan.settings.append(SettingChange(addon, 'DLHD_base', stored, target))
        plan.jobs.append('guide_retry')


def weather_is_fixed(engine):
    raw = engine.read('addons/weather.multi/lib/providers/yahoo.py')
    tree = ast.parse(raw)
    # The developer's newer implementation has case-insensitive lookups with a
    # default for unknown labels. Do not replace that implementation with ours.
    if any(isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == 'OUTLOOK' for n in ast.walk(tree)):
        return False
    if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'get_outlook' for n in ast.walk(tree)):
        return False
    conversions = ast.parse(engine.read('addons/weather.multi/lib/conversions.py'))
    methods = {n.name: n for n in conversions.body if isinstance(n, ast.FunctionDef)}
    outlook = methods.get('get_outlook')
    condition = methods.get('get_condition')
    if not outlook or not condition:
        return False
    expected = ast.parse("def get_outlook(label):\n    return OUTLOOK_LOWER.get(label.lower(), 'na')\n").body[0]
    expected_condition = ast.parse('def get_condition(label):\n    return CONDITION_LOWER.get(label.lower(), label)\n').body[0]
    assignments={t.id:n for n in conversions.body if isinstance(n,ast.Assign) for t in n.targets if isinstance(t,ast.Name)}
    for name,source in (('OUTLOOK_LOWER','OUTLOOK'),('CONDITION_LOWER','CONDITION')):
        wanted=ast.parse(name+' = {key.lower(): value for key, value in '+source+'.items()}\n').body[0]
        if name not in assignments or fingerprint(assignments[name])!=fingerprint(wanted):return False
    return fingerprint(outlook) == fingerprint(expected) and fingerprint(condition) == fingerprint(expected_condition)


def inspect_special(engine, plan, recipe):
    name = recipe.get('special')
    if name == 'calendar':
        calendar(engine, plan)
        return True
    if name == 'extendedinfo':
        extendedinfo(engine, plan)
        return True
    if name == 'reauth':
        reauth(engine, plan)
    elif name == 'liveguide':
        liveguide(engine, plan)
    elif name == 'weather' and weather_is_fixed(engine):
        plan.reason = 'The upstream Yahoo mapping already handles label case and missing icons'
        return True
    elif name == 'trakt_context':
        router = engine.read('addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/script/router.py')
        tree = ast.parse(router)
        if not any(isinstance(n, ast.Constant) and n.value == 'sync_options' for n in ast.walk(tree)):
            raise Incompatible('TMDb Helper sync-options route changed')
    return False
