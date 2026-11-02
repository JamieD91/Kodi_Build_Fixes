"""Pure structural rules for supported calendar mapper layouts."""
import ast
import textwrap

METHOD = """
    def get_params(self):
        params = super().get_params()
        params['season'] = self.meta.get('season')
        params['episode'] = self.meta.get('number')
        return params
"""


def find_class(tree, name, bases):
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    if len(classes) != 1:
        raise RuntimeError('Unsupported mapper structure: expected one ' + name + ' class. No changes made.')
    cls = classes[0]
    if [ast.unparse(base) for base in cls.bases] != bases or cls.decorator_list or cls.keywords:
        raise RuntimeError('Unsupported inheritance/decorators for ' + name + '. No changes made.')
    return cls


def without_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def defined_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {part.id for target in targets for part in ast.walk(target) if isinstance(part, ast.Name)}
    return set()


def method_matches(node, source):
    expected = ast.parse(textwrap.dedent(source)).body[0]
    # Ignore comments, formatting, quote style and a leading method docstring.
    actual = ast.FunctionDef(
        name=node.name, args=node.args, body=without_docstring(node.body),
        decorator_list=node.decorator_list, returns=node.returns, type_comment=node.type_comment)
    if getattr(node, 'type_params', None):
        return False
    return ast.dump(actual) == ast.dump(expected)


def validate_dependencies(raw):
    tree = ast.parse(raw.decode('utf-8-sig'))
    media = find_class(tree, 'MediaItemMapper', ['ItemMapper'])
    season = find_class(tree, 'SeasonItemMapper', ['MediaItemMapper'])
    episode = find_class(tree, 'EpisodeItemMapper', ['SeasonItemMapper'])
    expected_methods = {
        '__init__': '''
            def __init__(self, meta, add_infoproperties, sub_type=False):
                self.add_infoproperties = add_infoproperties
                self.meta = meta
                self.meta.update(self.meta.pop(sub_type, {})) if sub_type else None
        ''',
        'get_params': '''
            def get_params(self):
                params = {
                    'info': 'details',
                    'tmdb_id': self.tmdb_id,
                    'tmdb_type': self.tmdb_type,
                }
                return params
        ''',
    }
    for name, expected in expected_methods.items():
        nodes = [n for n in media.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(nodes) != 1 or not isinstance(nodes[0], ast.FunctionDef) or not method_matches(nodes[0], expected):
            raise RuntimeError('Unsupported MediaItemMapper.' + name + ' data flow. No changes made.')
    if any('params' in defined_names(node) for node in media.body):
        raise RuntimeError('MediaItemMapper overrides the parameter property. No changes made.')
    inherited_fix = False
    for cls in [season, episode]:
        for node in cls.body:
            names = defined_names(node)
            if names & {'__init__', 'params'}:
                raise RuntimeError('Unsupported ' + cls.name + ' parameter/constructor override. No changes made.')
            if 'get_params' in names:
                if cls is episode and isinstance(node, ast.FunctionDef) and method_matches(node, METHOD):
                    inherited_fix = True
                else:
                    raise RuntimeError('Unrecognised ' + cls.name + '.get_params override; it may already be fixed. No changes made.')
    return inherited_fix


def patch_source(raw):
    text = raw.decode('utf-8-sig')
    tree = ast.parse(text)
    imports = [alias for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == 'tmdbhelper.lib.items.directories.mapper_standard'
               for alias in node.names if alias.name == 'EpisodeItemMapper' and alias.asname is None]
    if len(imports) != 1:
        raise RuntimeError('Calendar episode mapper import changed. No changes made.')
    cls = find_class(tree, 'CalendarEpisodeItemMapper', ['CalendarItemMapper', 'EpisodeItemMapper'])
    mixin = find_class(tree, 'CalendarItemMapper', [])
    if any(defined_names(node) & {'get_params', 'params', '__init__'} for node in mixin.body):
        raise RuntimeError('CalendarItemMapper overrides the parameter data flow. No changes made.')
    factory = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'FactoryCalendarEpisodeItemMapper']
    expected_factory = '''
        def FactoryCalendarEpisodeItemMapper(meta, add_infoproperties=None):
            return CalendarEpisodeItemMapper(meta, add_infoproperties, sub_type='episode')
    '''
    if len(factory) != 1 or not method_matches(factory[0], expected_factory):
        raise RuntimeError('Unsupported calendar episode metadata factory. No changes made.')
    body = without_docstring(cls.body)
    methods = [node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if methods and (len(methods) != 1 or not isinstance(methods[0], ast.FunctionDef) or not method_matches(methods[0], METHOD)):
        raise RuntimeError('Calendar class contains unrecognised methods; it may already be fixed. No changes made.')
    assignments = [node for node in body if node not in methods]
    expected = ast.parse("class X:\n    tmdb_type = 'tv'\n    mediatype = 'episode'\n").body[0].body
    if sorted(ast.dump(node) for node in assignments) != sorted(ast.dump(node) for node in expected):
        raise RuntimeError('Unsupported calendar class configuration. No changes made.')
    if methods:
        return raw  # Recognise our repair without rewriting it or adding it twice.
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    lines.insert(cls.end_lineno, METHOD.replace('\n', newline))
    result = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + ''.join(lines).encode('utf-8')
    compile(result, 'mapper_calendar.py', 'exec')
    return result
