"""Cancellable local file-size scan, with bounded memory and no file writes."""
from dataclasses import dataclass, field
import heapq
import os
from pathlib import Path
import stat
import time

CATEGORIES = {
    'code': 'Add-ons: installed code and skins',
    'data': 'Add-ons: saved data and caches',
    'art': 'Thumbnails and artwork',
    'database': 'Kodi databases',
    'packages': 'Downloaded installer ZIPs',
    'temp': 'Temporary files, caches and logs',
    'profile': 'Profiles, settings and playlists',
    'other': 'Other Kodi files',
}


def normal(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def inside(path, parent):
    try:
        return os.path.commonpath([normal(path), normal(parent)]) == normal(parent)
    except ValueError:
        return False


def link_like(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


@dataclass
class Root:
    path: Path
    role: str
    title: str


def local_roots(candidates):
    """Collapse overlapping special paths. Explicit roots may resolve locally;
    symlinks and junctions encountered *inside* them are never traversed.
    """
    roots, excluded = [], []
    for value, role, title in candidates:
        text = os.fspath(value)
        if not text or '://' in text or text.startswith(('\\\\', '//')):
            excluded.append(title+': non-local or unavailable location')
            continue
        try:
            path = Path(text).resolve()
            if str(path) == path.anchor or str(path).startswith(('\\\\', '//')):
                excluded.append(title+': unsupported root location')
                continue
            if not path.is_dir():
                excluded.append(title+': folder unavailable')
                continue
        except (OSError, RuntimeError):
            excluded.append(title+': folder inaccessible')
            continue
        roots.append(Root(path, role, title))
    # Prefer home as the classification owner when special paths coincide.
    roots.sort(key=lambda root: (len(root.path.parts), root.role != 'home'))
    unique = []
    for root in roots:
        if not any(inside(root.path, prior.path) for prior in unique):
            unique.append(root)
    return unique, excluded


def classify(role, parts):
    parts = tuple(parts)
    if role == 'temp':
        return 'temp', None, None
    if role == 'home':
        if parts[:2] == ('addons', 'packages'):
            return 'packages', None, None
        if parts[:1] == ('addons',):
            return 'code', parts[1] if len(parts) > 2 else None, 'code'
        if parts[:1] in (('temp',), ('cache',)):
            return 'temp', None, None
        if parts[:1] != ('userdata',):
            return 'other', None, None
        parts = parts[1:]
    if parts[:1] == ('profiles',) and len(parts) > 2:
        parts = parts[2:]
    if parts[:1] == ('addon_data',):
        return 'data', parts[1] if len(parts) > 2 else None, 'data'
    if parts[:1] == ('Thumbnails',):
        return 'art', None, None
    if parts[:1] == ('Database',):
        return 'database', None, None
    return 'profile', None, None


@dataclass
class Directory:
    path: Path
    root: Root
    parent: object = None
    bytes: int = 0
    files: int = 0
    skipped: int = 0
    children: list = field(default_factory=list)
    scanned: bool = False
    categories: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    roots: list
    directories: dict = field(default_factory=dict)
    categories: dict = field(default_factory=lambda: {key: 0 for key in CATEGORIES})
    addons: dict = field(default_factory=dict)
    largest: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    error_count: int = 0
    files: int = 0
    bytes: int = 0
    links: int = 0
    shared_links: int = 0
    complete: bool = True
    elapsed: float = 0

    def label(self, path):
        for root in self.roots:
            if inside(path, root.path):
                relative = Path(path).relative_to(root.path).as_posix()
                return root.title+('/'+relative if relative != '.' else '')
        return Path(path).name

    def issue(self, path, reason):
        self.error_count += 1
        self.complete = False
        if len(self.errors) < 30:
            self.errors.append(self.label(path)+': '+reason)


def safe_path(path, root):
    """Reject links inserted into a previously scanned path before revisiting."""
    path, root = Path(path), Path(root)
    if not inside(path, root):
        return False
    try:
        current = path
        while True:
            if link_like(current.lstat()):
                return False
            if normal(current) == normal(root):
                return True
            current = current.parent
    except OSError:
        return False


def scan(roots, cancel=lambda: False, progress=lambda snapshot: None, max_directories=20000):
    snapshot = Snapshot(roots)
    started = time.monotonic()
    pending, visited, hard_links = [], [], set()
    for root in roots:
        node = Directory(root.path, root)
        snapshot.directories[normal(root.path)] = node
        pending.append(node)
    ticks = 0
    last_update = started
    while pending:
        if cancel():
            snapshot.complete = False
            snapshot.errors.append('Scan cancelled: these are partial results.')
            break
        node = pending.pop()
        visited.append(node)
        if not safe_path(node.path, node.root.path):
            snapshot.issue(node.path, 'folder changed or became a link')
            continue
        try:
            with os.scandir(node.path) as entries:
                for entry in entries:
                    ticks += 1
                    if ticks % 128 == 0:
                        now = time.monotonic()
                        if now-last_update >= 0.15:
                            progress(snapshot)
                            last_update = now
                        if cancel():
                            snapshot.complete = False
                            snapshot.errors.append('Scan cancelled: these are partial results.')
                            pending.clear()
                            break
                    path = Path(entry.path)
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if link_like(info):
                            snapshot.links += 1
                            node.skipped += 1
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if len(snapshot.directories) >= max_directories:
                                snapshot.issue(path, 'directory limit reached')
                                node.skipped += 1
                                continue
                            child = Directory(path, node.root, node)
                            node.children.append(child)
                            snapshot.directories[normal(path)] = child
                            pending.append(child)
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            node.skipped += 1
                            continue
                        # Windows DirEntry.stat may omit file IDs and link
                        # counts. A full stat is needed for hard-link accounting.
                        if os.name == 'nt' and (not info.st_ino or not info.st_nlink):
                            info = os.stat(path, follow_symlinks=False)
                        size = info.st_size
                        # Count shared hard-linked file content only once. The
                        # first encountered path owns its category attribution.
                        if info.st_nlink > 1 and info.st_ino:
                            identity = (info.st_dev, info.st_ino)
                            if identity in hard_links:
                                snapshot.shared_links += 1
                                size = 0
                            else:
                                hard_links.add(identity)
                        node.files += 1
                        node.bytes += size
                        snapshot.files += 1
                        snapshot.bytes += size
                        parts = path.relative_to(node.root.path).parts
                        category, addon_id, component = classify(node.root.role, parts)
                        snapshot.categories[category] += size
                        node.categories[category] = node.categories.get(category, 0)+size
                        if addon_id:
                            addon = snapshot.addons.setdefault(addon_id, {'code': 0, 'data': 0, 'code_paths': set(), 'data_paths': set()})
                            addon[component] += size
                            # Locate the containing add-on root, not each file.
                            marker = 'addons' if component == 'code' else 'addon_data'
                            index = parts.index(marker)
                            addon[component+'_paths'].add(node.root.path.joinpath(*parts[:index+2]))
                        candidate = (size, str(path))
                        if len(snapshot.largest) < 100:
                            heapq.heappush(snapshot.largest, candidate)
                        elif candidate > snapshot.largest[0]:
                            heapq.heapreplace(snapshot.largest, candidate)
                    except OSError as error:
                        node.skipped += 1
                        snapshot.issue(path, type(error).__name__)
            node.scanned = True
        except OSError as error:
            snapshot.issue(node.path, type(error).__name__)
    for node in reversed(visited):
        if node.parent is not None:
            node.parent.bytes += node.bytes
            node.parent.files += node.files
            node.parent.skipped += node.skipped
            for category, size in node.categories.items():
                node.parent.categories[category] = node.parent.categories.get(category, 0)+size
    snapshot.largest.sort(reverse=True)
    snapshot.elapsed = time.monotonic()-started
    return snapshot


def children(snapshot, path, limit=500, category=None):
    """Bounded, read-only directory browsing after the scan. Never open a file."""
    node = snapshot.directories.get(normal(path))
    if node is None:
        return [], 0
    if not safe_path(node.path, node.root.path):
        raise OSError('Folder changed after the scan.')
    result = []
    count = 0
    with os.scandir(node.path) as entries:
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
                if link_like(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child = snapshot.directories.get(normal(entry.path))
                    if child is None:
                        continue
                    size, folder = (child.categories.get(category, 0) if category else child.bytes), True
                elif stat.S_ISREG(info.st_mode):
                    if category and classify(node.root.role, Path(entry.path).relative_to(node.root.path).parts)[0] != category:
                        continue
                    size, folder = info.st_size, False
                else:
                    continue
                if category and size == 0:
                    continue
                count += 1
                # Largest 500 entries, not the first 500 in filesystem order.
                record = (size, entry.name, folder, entry.path)
                if len(result) < limit:
                    heapq.heappush(result, record)
                elif record > result[0]:
                    heapq.heapreplace(result, record)
            except OSError:
                continue
    return sorted(result, reverse=True), max(0, count-limit)
