"""Exercise the real defense methods without starting the game or Qt."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def test_defense_waits_between_portrait_clicks():
    tree = ast.parse(Path('tasks/battle/battle.py').read_text())
    battle_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Battle')
    battle_class.body = [
        method for method in battle_class.body
        if isinstance(method, ast.FunctionDef)
        and method.name in {'_calculate_skills_position', '_defense_this_round'}
    ]
    elapsed = 0.0
    clicks = []

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    auto = SimpleNamespace(
        find_element=lambda name: (399, 811) if name.endswith('gear_left.png') else (1372, 822),
        mouse_click=lambda *point: clicks.append((elapsed, point)),
        mouse_drag_link=Mock(),
        mouse_to_blank=Mock(),
    )
    namespace = {
        'auto': auto,
        'cfg': SimpleNamespace(set_win_size=1080, mouse_action_interval=0.5, simulator=False),
        'sleep': sleep,
    }
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), battle_class], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'tasks/battle/battle.py', 'exec'), namespace)
    assert namespace['Battle']._defense_this_round()
    assert len(clicks) == 7
    # Adjacent portraits must not be toggled in the same game frame. The
    # default 0.5-second setting must leave a positive delay on desktop too.
    assert all(b[0] - a[0] >= 0.3 for a, b in zip(clicks, clicks[1:]))
