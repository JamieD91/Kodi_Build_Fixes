"""Skin-native read-only storage exploration and dependency details."""
import json
from pathlib import Path
import shutil
import xbmc
import xbmcgui
import xbmcvfs
from jamiestorageviewer.scan import CATEGORIES, children, local_roots, normal, scan
from jamiestorageviewer.inventory import Dependencies, fallback_manifests, from_rpc

TITLE = 'Kodi Storage Viewer'


def size(value):
    number = float(value)
    for suffix in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if number < 1024 or suffix == 'TiB':
            return str(int(number))+' B' if suffix == 'B' else format(number, '.2f')+' '+suffix
        number /= 1024


def row(label, detail='', folder=True):
    item = xbmcgui.ListItem(label=label, label2=detail)
    item.setArt({'icon': 'DefaultFolder.png' if folder else 'DefaultFile.png'})
    return item


def choose(title, rows):
    return xbmcgui.Dialog().select(title, rows, useDetails=True)


def inventory():
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.GetAddons', 'params': {
        'enabled': 'all', 'installed': True,
        'properties': ['name', 'version', 'path', 'enabled', 'dependencies']}}
    try:
        reply = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
        items = reply['result']['addons']
        addons, complete = from_rpc(items)
        for addon in addons.values():
            if addon.path:
                addon.path = xbmcvfs.translatePath(addon.path)
        return addons, complete and bool(addons)
    except (ValueError, KeyError, TypeError):
        return {}, False


def capture():
    candidates = [(xbmcvfs.translatePath(path), role, title) for path, role, title in (
        ('special://home/', 'home', 'Kodi home'),
        ('special://masterprofile/', 'profile', 'Master profile'),
        ('special://profile/', 'profile', 'Current profile'),
        ('special://temp/', 'temp', 'Kodi temporary files'))]
    roots, excluded = local_roots(candidates)
    if not roots:
        xbmcgui.Dialog().ok(TITLE, 'No accessible local Kodi folders were found.')
        return None
    dialog = xbmcgui.DialogProgress()
    dialog.create(TITLE, 'Scanning local Kodi files. You can cancel at any time.')
    monitor = xbmc.Monitor()
    cancelled = lambda: dialog.iscanceled() or monitor.abortRequested()
    try:
        def progress(snapshot):
            dialog.update(0, format(snapshot.files, ',')+' files · '+size(snapshot.bytes)+
                '\nMeasuring file sizes; file contents are not opened by the size scan.')
        snapshot = scan(roots, cancel=cancelled, progress=progress)
        if monitor.abortRequested():
            return None
        dialog.update(0, 'Checking installed add-on dependencies and active-skin links...')
        addons, complete = inventory()
        if not complete:
            addons = fallback_manifests(snapshot, addons)
        dependencies = Dependencies(addons, complete)
        active_skin = xbmc.getSkinDir()
        profiles = [Path(value) for value, role, _ in candidates if role == 'profile' and '://' not in str(value)]
        if not cancelled():
            dependencies.find_references(snapshot, active_skin, profiles, cancel=cancelled)
        else:
            dependencies.reference_complete = False
        if monitor.abortRequested():
            return None
        return snapshot, dependencies, active_skin, excluded
    finally:
        dialog.close()


def overview(snapshot, graph, excluded):
    lines = [('Complete scan' if snapshot.complete else 'PARTIAL scan')+' · '+size(snapshot.bytes),
        format(snapshot.files, ',')+' file paths · '+format(len(snapshot.directories), ',')+' folders · '+
        format(snapshot.elapsed, '.1f')+' seconds', '']
    for key, value in sorted(snapshot.categories.items(), key=lambda item: item[1], reverse=True):
        lines.append(size(value)+' · '+CATEGORIES[key])
    lines.extend(['', 'Storage volumes (shared with other apps and files):'])
    volumes = set()
    for root in snapshot.roots:
        try:
            identity = (root.path.stat().st_dev, root.path.anchor)
            if identity in volumes:
                continue
            volumes.add(identity)
            usage = shutil.disk_usage(root.path)
            lines.append(root.title+': '+size(usage.free)+' free / '+size(usage.total)+' total')
        except OSError:
            lines.append(root.title+': volume free space unavailable')
    lines.extend(['', 'These are logical file-content sizes. Filesystem allocation, compression and '
        'Android app-private/APK storage can differ. Only the listed accessible local Kodi folders are scanned. '
        'Linked folders are not followed; overlapping special paths are scanned once. '
        'Files can change while Kodi runs.',
        'Shared hard-link duplicates counted once: '+str(snapshot.shared_links)+
        '. First encountered path owns category attribution.',
        'Skipped symbolic links / junctions: '+str(snapshot.links),
        'Unreadable / changed entries: '+str(snapshot.error_count),
        'Installed dependency inventory: '+('available' if graph.complete else 'INCOMPLETE'),
        'Active-skin / favourites reference check: '+('completed' if graph.reference_complete else 'partial / unavailable')])
    if excluded:
        lines.extend(['', 'Excluded locations:']+excluded)
    if snapshot.errors:
        lines.extend(['', 'Scan notes (first 30 errors):']+snapshot.errors)
    xbmcgui.Dialog().textviewer(TITLE, '\n'.join(lines))


def browse(snapshot, paths, category=None):
    paths = [Path(path) for path in paths if normal(path) in snapshot.directories]
    if not paths:
        xbmcgui.Dialog().ok(TITLE, 'No measured folders are available for this selection.')
        return
    if len(paths) > 1:
        choice = choose('Choose a Kodi folder', [row(snapshot.label(path)) for path in paths])
        if choice < 0:
            return
        current = paths[choice]
    else:
        current = paths[0]
    stack = []
    while True:
        node = snapshot.directories[normal(current)]
        total = node.categories.get(category, 0) if category else node.bytes
        try:
            entries, omitted = children(snapshot, current, category=category)
        except OSError:
            xbmcgui.Dialog().ok(TITLE, 'That folder changed or became inaccessible. Please rescan.')
            return
        labels = [row('Folder summary · '+size(total),
            (CATEGORIES[category]+' only · ' if category else '')+str(len(entries))+
            ' entries shown'+(' · '+str(omitted)+' smaller entries hidden' if omitted else ''))]
        labels.extend(row(size(value)+' · '+name, 'Folder' if folder else 'File', folder)
            for value, name, folder, _ in entries)
        selected = choose(snapshot.label(current), labels)
        if selected < 0:
            if stack:
                current = stack.pop()
                continue
            return
        if selected == 0:
            xbmcgui.Dialog().textviewer('Folder summary', snapshot.label(current)+'\n\n'+
                size(total)+' measured'+(' in '+CATEGORIES[category] if category else '')+
                '\n'+format(node.files, ',')+' file paths in full subtree\n'+str(node.skipped)+' skipped entries\n\n'+
                'Folder totals come from the scan. File rows show current sizes. '
                'Only the largest 500 entries in a folder are shown. No files are opened or deleted.')
            continue
        value, name, folder, path = entries[selected-1]
        if folder:
            stack.append(current)
            current = Path(path)
        else:
            xbmcgui.Dialog().textviewer('File details', snapshot.label(path)+'\n\n'+size(value)+
                '\n\nFile contents have not been opened. Use the owning add-on or Kodi settings to manage this file.')


def addon_sizes(snapshot, addon_id):
    return snapshot.addons.get(addon_id, {'code': 0, 'data': 0, 'code_paths': set(), 'data_paths': set()})


def dependency_details(graph, addon_id):
    addon = graph.addons.get(addon_id)
    def label(key):
        item = graph.addons.get(key)
        if item is None:
            return key
        state = 'enabled' if item.enabled is True else 'disabled' if item.enabled is False else 'state unknown'
        return item.name+' ('+key+') · '+state
    direct = graph.required.get(addon_id, set())
    indirect = graph.dependants(addon_id)-direct
    optional = graph.optional.get(addon_id, set())
    lines = [label(addon_id), '', 'Required by directly:']+[label(key) for key in sorted(direct)]
    if not direct:
        lines.append('None declared'+(' in the available inventory' if not graph.complete else ''))
    lines.extend(['', 'Required by indirectly:']+[label(key) for key in sorted(indirect)])
    lines.extend(['', 'Optional dependency of:']+[label(key) for key in sorted(optional)])
    lines.extend(['', 'Linked from active skin / favourites:']+sorted(graph.references.get(addon_id, set())))
    lines.extend(['', 'This add-on declares these dependencies:']+
        [label(key)+(' · optional' if opt else ' · required') for key, opt in addon.dependencies] if addon else [])
    lines.extend(['', 'An absence of declared dependants is not a guarantee that an add-on is unused. '
        'Custom skins and add-ons can call other add-ons dynamically. Kodi manages removal and its own dependency checks.'])
    if not graph.complete or not graph.reference_complete:
        lines.append('Some dependency or active-skin reference information is unavailable.')
    xbmcgui.Dialog().textviewer('Add-on dependencies', '\n'.join(lines))


class OpenManager(Exception):
    pass


def addon_detail(snapshot, graph, addon_id, active_skin):
    addon = graph.addons.get(addon_id)
    name = addon.name if addon else addon_id
    while True:
        values = addon_sizes(snapshot, addon_id)
        selected = choose(name, [
            row('Size and status', size(values['code']+values['data'])+' measured · '+graph.status(addon_id, active_skin)),
            row('Dependencies and skin / favourites links'),
            row('Browse installed code · '+size(values['code'])),
            row('Browse saved data · '+size(values['data'])),
            row('Open Kodi add-on manager', 'Use Kodi to review, disable or uninstall add-ons')])
        if selected < 0:
            return
        if selected == 0:
            state = 'Enabled' if addon and addon.enabled is True else 'Disabled' if addon and addon.enabled is False else 'State unavailable'
            text = (name+'\n'+addon_id+'\n'+(addon.version if addon else '')+'\n\n'+state+'\n'+
                graph.status(addon_id, active_skin)+'\n\nInstalled code: '+size(values['code'])+
                '\nSaved data / caches: '+size(values['data'])+'\nTotal measured: '+size(values['code']+values['data']))
            if not values['code_paths'] and addon:
                text += '\n\nCode is built-in, outside the scanned folders, empty or inaccessible; zero measured bytes does not mean it occupies no space.'
            if not addon:
                text += '\n\nNo installed add-on matched these local files'+(' in the current inventory.' if graph.complete else '; inventory is incomplete.')
            if addon_id == active_skin:
                text += '\n\nThis is the active skin.'
            xbmcgui.Dialog().textviewer('Add-on storage', text)
        elif selected == 1:
            dependency_details(graph, addon_id)
        elif selected in (2, 3):
            browse(snapshot, sorted(values['code_paths' if selected == 2 else 'data_paths']))
        elif selected == 4:
            xbmcgui.Dialog().ok(TITLE, 'Opening My add-ons. Find '+name+' to review its information and removal options. '
                'The viewer has not disabled or removed anything.')
            raise OpenManager()


def addons_menu(snapshot, graph, active_skin, mode='all'):
    ids = set(graph.addons) | set(snapshot.addons)
    if mode == 'no-dependants':
        ids = {key for key in ids if key in graph.addons and not graph.dependants(key)}
    elif mode == 'unmatched':
        ids = {key for key in snapshot.addons if key not in graph.addons and addon_sizes(snapshot, key)['data'] > 0}
    ids = sorted(ids, key=lambda key: (-(addon_sizes(snapshot, key)['code']+addon_sizes(snapshot, key)['data']),
        graph.addons[key].name.casefold() if key in graph.addons else key))
    if not ids:
        xbmcgui.Dialog().ok(TITLE, 'No matching add-ons or saved-data folders were found.')
        return
    while True:
        rows = []
        for key in ids:
            values = addon_sizes(snapshot, key)
            name = graph.addons[key].name if key in graph.addons else key
            state = graph.status(key, active_skin) if key in graph.addons else 'No installed add-on matched'
            rows.append(row(size(values['code']+values['data'])+' · '+name,
                'Code '+size(values['code'])+' · Data '+size(values['data'])+' · '+state))
        selected = choose('Add-ons by measured size'+(' · '+mode if mode != 'all' else ''), rows)
        if selected < 0:
            return
        addon_detail(snapshot, graph, ids[selected], active_skin)


def help_text():
    xbmcgui.Dialog().textviewer(TITLE,
        'Scan on demand, explore by category or folder, and compare add-on code plus saved data. '
        'The size scan uses file metadata; it does not open media, database contents, logs or account settings. '
        'Small add-on manifests and bounded active-skin/favourites XML are read for dependency/reference information.\n\n'
        'Required dependants include enabled and disabled installed add-ons, directly and indirectly. '
        'Optional dependencies and links in the active skin/favourites are shown separately. '
        'Undeclared/dynamic calls cannot all be discovered. No declared dependants does not mean safe to delete.\n\n'
        'Unmatched data may belong to an uninstalled add-on, an embedded build component or an add-on missing from '
        'the available inventory. It is not automatically classified as disposable.\n\n'
        'This viewer measures accessible local Kodi folders, not the complete Android app allocation or other apps. '
        'It skips links/junctions and remote locations, avoids overlapping root totals and reports partial scans. '
        'Shared hard links are counted once. Sizes are logical file contents, so volume allocation can differ.\n\n'
        'No files are deleted and no add-ons are disabled. Open Kodi add-on manager to use its normal controls, '
        'then reopen/rescan the viewer. Scans stay in memory; no scan report is uploaded or saved. '
        'Only the largest 500 entries per folder and 100 files overall are kept in the display.')


def main():
    try:
        captured = capture()
        while captured is not None:
            snapshot, graph, active_skin, excluded = captured
            choice = choose(TITLE+' · '+size(snapshot.bytes)+(' · PARTIAL' if not snapshot.complete else ''), [
                row('Storage overview', str(snapshot.files)+' files · volumes, scan scope and skipped entries'),
                row('Browse by category', 'Add-ons, data, artwork, databases, installer ZIPs, temp and profiles'),
                row('Add-ons by total size', 'Installed code plus saved data · largest first'),
                row('Add-ons with no declared dependants', 'Review dependencies and skin links before deciding'),
                row('Unmatched add-on data', 'Data folders with no match in the installed inventory'),
                row('Largest files', 'Top 100 measured files'),
                row('Browse Kodi folders', 'Explore measured local storage'),
                row('Rescan', 'Refresh sizes and dependency information'),
                row('Open Kodi add-on manager', 'Kodi controls disabling and removal'),
                row('Help / how sizes and dependencies work')])
            if choice < 0:
                return
            if choice == 0:
                overview(snapshot, graph, excluded)
            elif choice == 1:
                keys = sorted(CATEGORIES, key=lambda key: snapshot.categories[key], reverse=True)
                selected = choose('Storage categories', [row(size(snapshot.categories[key])+' · '+CATEGORIES[key],
                    format(100*snapshot.categories[key]/max(snapshot.bytes, 1), '.1f')+'% of measured Kodi files') for key in keys])
                if selected >= 0:
                    category = keys[selected]
                    browse(snapshot, [root.path for root in snapshot.roots
                        if snapshot.directories[normal(root.path)].categories.get(category, 0)], category)
            elif choice in (2, 3, 4):
                addons_menu(snapshot, graph, active_skin, {2:'all', 3:'no-dependants', 4:'unmatched'}[choice])
            elif choice == 5:
                selected = choose('Largest measured files', [row(size(value)+' · '+Path(path).name,
                    snapshot.label(path), False) for value, path in snapshot.largest])
                if selected >= 0:
                    value, path = snapshot.largest[selected]
                    xbmcgui.Dialog().textviewer('File details', snapshot.label(path)+'\n\n'+size(value)+
                        '\n\nNo file contents have been opened.')
            elif choice == 6:
                browse(snapshot, [root.path for root in snapshot.roots])
            elif choice == 7:
                captured = capture()
            elif choice == 8:
                raise OpenManager()
            else:
                help_text()
    except OpenManager:
        xbmc.executebuiltin('ActivateWindow(AddonBrowser,addons://user/all,return)')
    except Exception as error:
        xbmc.log('Kodi Storage Viewer: '+type(error).__name__, xbmc.LOGERROR)
        xbmcgui.Dialog().ok(TITLE, 'Unable to complete the view ('+type(error).__name__+'). '
            'No cleanup operation was performed. See the Kodi log.')
