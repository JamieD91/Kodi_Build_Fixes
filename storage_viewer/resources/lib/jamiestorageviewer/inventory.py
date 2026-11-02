"""Dependency information and bounded active-skin references; no Kodi imports."""
from dataclasses import dataclass, field
import codecs
from pathlib import Path
import os
import re
import xml.etree.ElementTree as ET
from jamiestorageviewer.scan import inside, normal, safe_path

VALID_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}\Z')


@dataclass
class Addon:
    id: str
    name: str
    version: str = ''
    kind: str = ''
    enabled: object = None
    path: str = ''
    dependencies: list = field(default_factory=list)


def from_rpc(items):
    addons, complete = {}, True
    for item in items:
        if not isinstance(item, dict):
            complete = False
            continue
        addon_id = item.get('addonid', '')
        if not isinstance(addon_id, str) or not VALID_ID.fullmatch(addon_id):
            complete = False
            continue
        dependencies = item.get('dependencies')
        if not isinstance(dependencies, list):
            complete = False
            dependencies = []
        parsed = []
        for dep in dependencies:
            if not isinstance(dep, dict) or not isinstance(dep.get('addonid'), str) or not VALID_ID.fullmatch(dep['addonid']):
                complete = False
                continue
            if not isinstance(dep.get('optional'), bool):
                complete = False
            parsed.append((dep['addonid'], dep.get('optional') is True))
        addons[addon_id] = Addon(addon_id, item.get('name') or addon_id, item.get('version') or '',
            item.get('type') or '', item.get('enabled'), item.get('path') or '', parsed)
    return addons, complete


def read_small_xml(path, limit=1024*1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit+1)
    if len(raw) > limit or b'<!DOCTYPE' in raw.upper():
        raise ValueError('Unsupported or oversized XML metadata.')
    return raw


def read_references(path, pattern, limit, cancel=lambda: False, chunk_size=65536):
    """Stream even large generated skin XML without parsing or retaining it.
    No XML entities are expanded. Keep enough overlap for an ID spanning chunks
    and defer a match at a chunk's right edge until its boundary is known.
    """
    found, used, tail = set(), 0, ''
    overlap = 204  # validated add-on IDs contain at most 200 characters
    buffer_at_start = True
    with Path(path).open('rb') as stream:
        initial = stream.read(min(chunk_size, limit))
        if initial.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = 'utf-32'
        elif initial.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = 'utf-16'
        elif initial.startswith(b'\x00<\x00?'):
            encoding = 'utf-16-be'
        elif initial.startswith(b'<\x00?\x00'):
            encoding = 'utf-16-le'
        else:
            encoding = 'utf-8-sig'
        decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
        raw = initial
        while raw:
            if cancel():
                return found, used, False, True
            used += len(raw)
            buffer = tail+decoder.decode(raw)
            for match in pattern.finditer(buffer):
                if match.end() < len(buffer) and (buffer_at_start or match.start() > 0):
                    found.add(match.group())
            tail = buffer[-overlap:]
            if len(buffer) > overlap:
                buffer_at_start = False
            raw = stream.read(min(chunk_size, limit-used)) if used < limit else b''
        buffer = tail+decoder.decode(b'', final=True)
        truncated = bool(stream.read(1))
        for match in pattern.finditer(buffer):
            if (buffer_at_start or match.start() > 0) and (not truncated or match.end() < len(buffer)):
                found.add(match.group())
    return found, used, truncated, False


def fallback_manifests(snapshot, addons):
    """Fill local metadata gaps; caller still marks the inventory incomplete."""
    result = dict(addons)
    for addon_id, sizes in snapshot.addons.items():
        if addon_id in result or not VALID_ID.fullmatch(addon_id):
            continue
        for folder in sizes['code_paths']:
            try:
                root = next(root.path for root in snapshot.roots if inside(folder, root.path))
                manifest = folder/'addon.xml'
                if not safe_path(manifest, root):
                    continue
                xml = ET.fromstring(read_small_xml(manifest))
                if xml.get('id') != addon_id:
                    continue
                deps = [(d.get('addon'), d.get('optional', '').lower() == 'true')
                        for d in xml.findall('requires/import') if VALID_ID.fullmatch(d.get('addon') or '')]
                extensions = [e.get('point', '') for e in xml.findall('extension')]
                result[addon_id] = Addon(addon_id, xml.get('name') or addon_id, xml.get('version') or '',
                    next((e for e in extensions if e != 'xbmc.addon.metadata'), ''), None, str(folder), deps)
                break
            except (OSError, ValueError, ET.ParseError, StopIteration):
                continue
    return result


class Dependencies:
    def __init__(self, addons, complete=True):
        self.addons = addons
        self.complete = complete
        self.required = {}
        self.optional = {}
        self.references = {}
        self.reference_complete = True
        self.cached = {}
        for addon in addons.values():
            for dependency, optional in addon.dependencies:
                table = self.optional if optional else self.required
                table.setdefault(dependency, set()).add(addon.id)

    def dependants(self, addon_id):
        if addon_id in self.cached:
            return self.cached[addon_id]
        visited, pending = {addon_id}, list(self.required.get(addon_id, set()))
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self.required.get(current, set())-visited)
        visited.discard(addon_id)
        self.cached[addon_id] = visited
        return visited

    def status(self, addon_id, active_skin=''):
        if addon_id == active_skin:
            return 'Active skin'
        required = self.dependants(addon_id)
        active = [key for key in required if self.addons[key].enabled is True]
        if active:
            return 'Required by '+str(len(active))+' enabled add-on(s)'
        if required:
            return 'Required by '+str(len(required))+' other installed add-on(s)'
        if self.references.get(addon_id):
            return 'Linked from skin / favourites'
        if self.optional.get(addon_id):
            return 'Optional dependency of '+str(len(self.optional[addon_id]))+' add-on(s)'
        return 'No declared dependants' if self.complete else 'Dependency inventory incomplete'

    def find_references(self, snapshot, active_skin, profile_paths, cancel=lambda: False):
        """Only read bounded XML from the active skin and profile links. Never
        retain XML contents (which may contain private settings) in results.
        """
        if not self.addons:
            self.reference_complete = False
            return
        pattern = re.compile(r'(?<![A-Za-z0-9_.-])(?:'+
            '|'.join(re.escape(key) for key in sorted(self.addons, key=len, reverse=True))+r')(?![A-Za-z0-9_.-])')
        skin = self.addons.get(active_skin)
        skin_folders = list(snapshot.addons.get(active_skin, {}).get('code_paths', set()))
        if skin and skin.path and '://' not in skin.path:
            skin_folders.append(Path(skin.path))
        candidates = []
        if skin_folders:
            for node in snapshot.directories.values():
                if any(inside(node.path, folder) for folder in skin_folders):
                    candidates.append((node.path, True))
        elif active_skin:
            self.reference_complete = False
        if active_skin and not candidates:
            self.reference_complete = False
        def files():
            for profile in profile_paths:
                yield Path(profile)/'favourites.xml'
                yield Path(profile)/'addon_data'/active_skin/'settings.xml'
            for folder, _ in candidates:
                node = snapshot.directories.get(normal(folder))
                if node is None or not safe_path(folder, node.root.path):
                    self.reference_complete = False
                    continue
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            if entry.name.lower().endswith('.xml') and entry.is_file(follow_symlinks=False):
                                yield Path(entry.path)
                except OSError:
                    self.reference_complete = False
        budget, attempts = 32*1024*1024, 0
        seen = set()
        for path in files():
            if normal(path) in seen:
                continue
            seen.add(normal(path))
            attempts += 1
            if attempts > 20000:
                self.reference_complete = False
                break
            if attempts % 16 == 0 and cancel():
                self.reference_complete = False
                break
            try:
                root = next(root.path for root in snapshot.roots if inside(path, root.path))
                if not safe_path(path, root):
                    continue
                if budget <= 0:
                    self.reference_complete = False
                    break
                found, used, truncated, cancelled = read_references(path, pattern, min(16*1024*1024, budget), cancel)
                budget -= used
                if truncated or cancelled:
                    self.reference_complete = False
                for match in found:
                    if match != active_skin:
                        self.references.setdefault(match, set()).add(snapshot.label(path))
                if cancelled:
                    break
            except FileNotFoundError:
                pass
            except (OSError, ValueError, ET.ParseError, StopIteration):
                self.reference_complete = False
