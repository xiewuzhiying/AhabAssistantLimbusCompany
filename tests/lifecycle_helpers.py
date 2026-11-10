"""Load production lifecycle methods without importing live game collaborators."""
import ast
from pathlib import Path


def load_source(path, namespace, *, functions=(), class_name=None, methods=None, base=None):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == class_name:
            if methods is not None:
                node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in methods]
            if base is not None:
                node.bases = [ast.Name(id='TestBase', ctx=ast.Load())]
                node.keywords = []
                namespace['TestBase'] = base
            nodes.append(node)
    nodes.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, 'exec'), namespace)
    return namespace.get(class_name)
