"""Version-independent, structure-checked repairs and guarded rollback.

This module has no Kodi dependency so the same transaction code can be tested
against ordinary folders without touching an installed Kodi profile.
"""
import ast
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import textwrap
import uuid
import xml.etree.ElementTree as ET


class Incompatible(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    """Stable across Python 3.9-3.14; comments/docstrings do not affect matching."""
    if isinstance(value, ast.AST):
        fields = {}
        for name, child in ast.iter_fields(value):
            if name == 'type_params' and not child:
                continue
            if name == 'body' and isinstance(value, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child = list(child)
                if child and isinstance(child[0], ast.Expr) and isinstance(child[0].value, ast.Constant) and isinstance(child[0].value.value, str):
                    child = child[1:]
            fields[name] = canonical(child)
        return [type(value).__name__, fields]
    if isinstance(value, list):
        return [canonical(v) for v in value]
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    return value


def fingerprint(node):
    return sha(json.dumps(canonical(node), sort_keys=True, separators=(',', ':')).encode())


def parse_snippet(text):
    return ast.parse(textwrap.dedent(text)).body


def scope_node(tree, scope):
    current = tree
    for name in scope:
        found = [n for n in current.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name]
        if len(found) != 1:
            raise Incompatible('Expected one code scope: ' + '.'.join(scope))
        current = found[0]
    return current


def statement_lists(node):
    for name, value in ast.iter_fields(node):
        if isinstance(value, list):
            if value and all(isinstance(n, ast.stmt) for n in value):
                yield value
            for child in value:
                if isinstance(child, ast.AST):
                    yield from statement_lists(child)
        elif isinstance(value, ast.AST):
            yield from statement_lists(value)


def block_matches(node, pattern):
    wanted = [fingerprint(n) for n in pattern]
    found = []
    for items in statement_lists(node):
        for i in range(len(items) - len(pattern) + 1):
            candidate = items[i:i + len(pattern)]
            if [fingerprint(n) for n in candidate] == wanted:
                found.append(candidate)
    return found


def source_slice(text, node):
    return ''.join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def format_snippet(text, indent, newline):
    clean = textwrap.dedent(text).strip('\r\n')
    return ''.join(indent + line + newline if line else newline for line in clean.splitlines())


def patch_python(raw, operations):
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = raw.decode('utf-8-sig')
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    edits = []
    for op in operations:
        kind = op['kind']
        scope = scope_node(tree, op.get('scope', []))
        after = parse_snippet(op['after'])
        if kind == 'definition':
            wanted = after[0]
            found = [n for n in scope.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == wanted.name]
            if len(found) > 1:
                raise Incompatible('Duplicate definition: ' + wanted.name)
            if found and fingerprint(found[0]) == fingerprint(wanted):
                continue
            allowed = [parse_snippet(s)[0] for s in op.get('before', []) if s.strip()]
            if found and not any(fingerprint(found[0]) == fingerprint(n) for n in allowed):
                raise Incompatible('Changed implementation: ' + wanted.name)
            if not found:
                if not op.get('allow_missing'):
                    raise Incompatible('Missing definition: ' + wanted.name)
                if isinstance(scope, ast.Module):
                    candidates = [n.lineno - 1 for n in scope.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
                    index = min(candidates) if candidates else len(lines)
                    indent = ''
                else:
                    index = scope.end_lineno
                    candidates = [n for n in scope.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
                    if not candidates:
                        raise Incompatible('Class has no known indentation')
                    first = candidates[0]
                    indent = lines[first.lineno - 1][:first.col_offset]
                edits.append((index, index, format_snippet(op['after'], indent, newline) + newline))
            else:
                node = found[0]
                start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])]) - 1
                indent = lines[node.lineno - 1][:node.col_offset]
                edits.append((start, node.end_lineno, format_snippet(op['after'], indent, newline)))
        elif kind == 'block':
            after_matches = block_matches(scope, after)
            if len(after_matches) == 1:
                continue
            if len(after_matches) > 1:
                raise Incompatible('Replacement block is ambiguous')
            candidates = []
            for option in op['before']:
                candidates.extend(block_matches(scope, parse_snippet(option)))
            unique = {(nodes[0].lineno, nodes[-1].end_lineno): nodes for nodes in candidates}
            if len(unique) != 1:
                raise Incompatible('Source block changed or is ambiguous')
            nodes = next(iter(unique.values()))
            indent = lines[nodes[0].lineno - 1][:nodes[0].col_offset]
            edits.append((nodes[0].lineno - 1, nodes[-1].end_lineno, format_snippet(op['after'], indent, newline)))
        elif kind == 'module_block':
            complete = block_matches(tree, after)
            if len(complete) == 1:
                continue
            if complete:
                raise Incompatible('Helper block is ambiguous')
            names = set(op['names'])
            existing = []
            for node in tree.body:
                defined = {getattr(node, 'name', '')}
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    defined.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
                if defined & names:
                    existing.append(node)
            if existing:
                if [fingerprint(n) for n in existing] != [fingerprint(n) for n in after]:
                    raise Incompatible('Existing helper block differs')
                continue
            candidates = [n.lineno - 1 for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
            index = min(candidates) if candidates else len(lines)
            edits.append((index, index, format_snippet(op['after'], '', newline) + newline))
        elif kind == 'import_json':
            if any(isinstance(n, ast.Import) and any(a.name == 'json' and a.asname in (None, 'json') for a in n.names) for n in tree.body):
                continue
            if any(isinstance(n, ast.Name) and n.id == 'json' and isinstance(n.ctx, ast.Store) for n in ast.walk(tree)):
                raise Incompatible('json name is used for another purpose')
            indexes = [n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            edits.append((max(indexes, default=0), max(indexes, default=0), 'import json' + newline))
        else:
            raise Incompatible('Unknown repair operation')
    # Reject overlapping replacements; insertions at a boundary are supported.
    ranges = sorted((s, e) for s, e, _ in edits if s != e)
    if any(b[0] < a[1] for a, b in zip(ranges, ranges[1:])):
        raise Incompatible('Overlapping repair operations')
    for start, end, replacement in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        lines[start:end] = [replacement]
    result = (b'\xef\xbb\xbf' if bom else b'') + ''.join(lines).encode('utf-8')
    compile(result, '<repaired addon>', 'exec')
    return result


def safe_path(root, relative):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('..', '.', '') or ':' in p or '\\' in p for p in rel.parts):
        raise Incompatible('Invalid repair path')
    root = Path(root).resolve()
    target = root.joinpath(*rel.parts)
    resolved = target.resolve()
    if resolved == root or root not in resolved.parents:
        raise Incompatible('Repair path escapes Kodi profile')
    for parent in (target,) + tuple(target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise Incompatible('Symlinked repair paths are unsupported')
    return target


@dataclass
class FileChange:
    path: str
    before: bytes
    after: bytes


@dataclass
class SettingChange:
    addon: str
    key: str
    before: str
    after: str


@dataclass
class Plan:
    id: str
    title: str
    status: str = 'fixed'
    reason: str = ''
    files: list = field(default_factory=list)
    settings: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    versions: dict = field(default_factory=dict)


class Engine:
    def __init__(self, root, profile, backend, catalog):
        self.root, self.profile = Path(root).resolve(), Path(profile).resolve()
        self.backend, self.catalog = backend, catalog
        self.profile.mkdir(parents=True, exist_ok=True)

    def read(self, relative):
        return safe_path(self.root, relative).read_bytes()

    def addon_version(self, addon):
        raw = self.read('addons/' + addon + '/addon.xml')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            if addon != 'script.extendedinfo':
                raise
            import re
            header = re.search(rb'<addon\b[^>]*>', raw)
            if not header:
                raise Incompatible('Missing add-on identity')
            root = ET.fromstring(header[0][:-1] + b'/>')
        if root.get('id') != addon:
            raise Incompatible('Unexpected add-on identity')
        return root.get('version', '?')

    def source_repair(self, plan, source):
        candidates = [p for p in source['paths'] if safe_path(self.root, p).is_file()]
        if not candidates:
            if source.get('optional'):
                return
            raise FileNotFoundError('Affected module is not installed in a supported location')
        for relative in candidates:
            owner=relative.split('/')[1]
            plan.versions[owner]=self.addon_version(owner)
            raw = self.read(relative)
            changed = patch_python(raw, source['operations'])
            if changed != raw:
                plan.files.append(FileChange(relative, raw, changed))

    def scan(self, selected=None):
        from buildfixes.special import inspect_special
        result = []
        for recipe in self.catalog['fixes']:
            if selected and recipe['id'] not in selected:
                continue
            plan = Plan(recipe['id'], recipe['title'])
            try:
                for addon in recipe.get('addons', []):
                    try:
                        plan.versions[addon] = self.addon_version(addon)
                    except FileNotFoundError:
                        if addon not in recipe.get('optional_addons', []):
                            raise
                special_done = inspect_special(self, plan, recipe)
                if not special_done:
                    for source in recipe.get('sources', []):
                        self.source_repair(plan, source)
                if plan.files or plan.settings or plan.jobs:
                    plan.status = 'ready'
                    plan.jobs = list(dict.fromkeys(plan.jobs + recipe.get('jobs', [])))
                    plan.reason = 'Compatible code structure; ready to repair'
                elif not plan.reason:
                    plan.reason = 'Already repaired, fixed upstream, or no change needed'
            except FileNotFoundError:
                plan.status, plan.reason = 'missing', 'Required add-on or supported module is not installed'
                plan.files.clear(); plan.settings.clear(); plan.jobs.clear()
            except (Incompatible, SyntaxError, ET.ParseError, ValueError, KeyError, TypeError) as error:
                plan.status, plan.reason = 'incompatible', str(error)
                plan.files.clear(); plan.settings.clear(); plan.jobs.clear()
            result.append(plan)
        return result

    @contextmanager
    def lock(self):
        path = self.profile / 'repair.lock'
        if path.exists():
            try:
                prior_pid=int(path.read_text())
            except (OSError, ValueError):
                raise Busy('An interrupted repair lock needs inspection')
            # Kodi has one process per profile. A previous Kodi process cannot
            # still own this profile after a restart. Never use os.kill(pid, 0)
            # here: its behaviour is unsafe on Windows.
            if prior_pid != os.getpid():
                path.unlink()
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise Busy('Another repair is running; restart Kodi if an earlier repair was interrupted')
        try:
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            yield
        finally:
            path.unlink(missing_ok=True)

    def atomic_write(self, relative, raw):
        target = safe_path(self.root, relative)
        temp = target.with_name(target.name + '.buildfixes-' + uuid.uuid4().hex)
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
        try:
            with open(temp, 'xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            try:
                os.chmod(temp, mode)
            except OSError:
                pass  # Android shared storage may impose its own mode.
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def queue_jobs(self, jobs):
        if not jobs:
            return
        path = self.profile / 'pending.json'
        pending = json.loads(path.read_text()) if path.exists() else {'jobs': []}
        pending['jobs'] = list(dict.fromkeys(pending['jobs'] + jobs))
        pending['origin_pid'] = os.getpid()
        self.write_json(path, pending)

    @staticmethod
    def write_json(path, value):
        path = Path(path)
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def apply(self, plan):
        if plan.status != 'ready':
            raise Incompatible('This repair is not ready to apply')
        if self.backend.is_playing():
            raise Busy('Stop playback before applying repairs')
        with self.lock():
            for change in plan.files:
                if self.read(change.path) != change.before:
                    raise Busy('An add-on changed since the scan; scan again')
            for change in plan.settings:
                if self.backend.get_setting(change.addon, change.key) != change.before:
                    raise Busy('A setting changed since the scan; scan again')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            folder = self.profile / 'backups' / (stamp + '-' + plan.id)
            folder.mkdir(parents=True)
            record = {'id': plan.id, 'title': plan.title, 'status': 'prepared', 'versions': plan.versions,
                      'files': [], 'settings': [], 'jobs': plan.jobs, 'created': stamp}
            for i, change in enumerate(plan.files):
                backup = 'file-%03d.bin' % i
                (folder / backup).write_bytes(change.before)
                assert (folder / backup).read_bytes() == change.before
                record['files'].append({'path': change.path, 'backup': backup,
                                        'before': sha(change.before), 'after': sha(change.after)})
            for change in plan.settings:
                record['settings'].append(vars(change).copy())
            self.write_json(folder / 'manifest.json', record)
            changed_files, changed_settings = [], []
            try:
                for change in plan.files:
                    if self.read(change.path) != change.before:
                        raise Busy('Target changed during installation')
                    self.atomic_write(change.path, change.after)
                    changed_files.append(change)
                    if self.read(change.path) != change.after:
                        raise OSError('Installed file verification failed')
                for change in plan.settings:
                    if self.backend.get_setting(change.addon, change.key) != change.before:
                        raise Busy('Setting changed during installation')
                    self.backend.set_setting(change.addon, change.key, change.after)
                    changed_settings.append(change)
                    if self.backend.get_setting(change.addon, change.key) != change.after:
                        raise OSError('Installed setting verification failed')
                self.queue_jobs(plan.jobs)
                record['status'] = 'installed'
            except Exception:
                for change in reversed(changed_settings):
                    self.backend.set_setting(change.addon, change.key, change.before)
                for change in reversed(changed_files):
                    self.atomic_write(change.path, change.before)
                record['status'] = 'rolled_back'
                self.write_json(folder / 'manifest.json', record)
                raise
            self.write_json(folder / 'manifest.json', record)
            return folder

    def backups(self):
        base = self.profile / 'backups'
        return sorted(base.glob('*/manifest.json'), reverse=True) if base.exists() else []

    def undo(self, manifest_path):
        if self.backend.is_playing():
            raise Busy('Stop playback before undoing a repair')
        path = Path(manifest_path).resolve()
        backups = (self.profile / 'backups').resolve()
        if backups not in path.parents or path.name != 'manifest.json':
            raise Incompatible('Backup is outside this toolkit profile')
        record = json.loads(path.read_text())
        if record.get('status') not in ('installed','prepared'):
            raise Incompatible('This backup is not an active installed repair')
        changes = []
        with self.lock():
            for item in record['files']:
                current = self.read(item['path'])
                raw = (path.parent / item['backup']).read_bytes()
                if sha(raw) != item['before']:
                    raise Incompatible('Backup checksum does not match')
                if record['status']=='prepared' and sha(current)==item['before']:
                    continue
                if sha(current) != item['after']:
                    raise Incompatible('An add-on changed after this repair; undo would overwrite newer code')
                changes.append(FileChange(item['path'], current, raw))
            for item in record['settings']:
                current=self.backend.get_setting(item['addon'], item['key'])
                if record['status']=='prepared' and current==item['before']:
                    continue
                if current != item['after']:
                    raise Incompatible('A setting changed after this repair; undo was stopped')
            written, settings = [], []
            try:
                for change in changes:
                    self.atomic_write(change.path, change.after); written.append(change)
                for item in record['settings']:
                    self.backend.set_setting(item['addon'], item['key'], item['before']); settings.append(item)
                self.queue_jobs([j for j in record.get('jobs', []) if j != 'trakt_metadata'])
            except Exception:
                for item in reversed(settings):self.backend.set_setting(item['addon'], item['key'], item['after'])
                for change in reversed(written):self.atomic_write(change.path, change.before)
                raise
            record['status'] = 'restored'
            self.write_json(path, record)
