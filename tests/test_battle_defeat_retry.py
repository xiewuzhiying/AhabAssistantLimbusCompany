"""Replay delayed defeat dialogs through the real battle loop."""

import ast
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def replay(monkeypatch, frames, defense_state=None):
    tree = ast.parse(Path('tasks/battle/battle.py').read_text())
    battle = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Battle')
    battle.body = [n for n in battle.body if isinstance(n, ast.FunctionDef)
                   and n.name in {'__init__', 'fight', '_confirm_defense_started'}]
    for method in battle.body:
        method.decorator_list = []
    namespace = {
        'auto': Mock(), 'cfg': SimpleNamespace(set_win_size=1080, fight_to_last_man=True),
        'log': Mock(), 'time': time, 'sleep': lambda _: None,
    }
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), battle], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'tasks/battle/battle.py', 'exec'), namespace)
    instance = namespace['Battle']()
    instance._update_wait_time = lambda *args: 0.1
    instance.operation_budgets = []

    def operate(**kwargs):
        state = kwargs['defense_for_solo_state']
        if state is not None:
            instance.operation_budgets.append(state.remaining_turns)
            state.remaining_turns -= 1
        return False

    instance._battle_operation = Mock(side_effect=operate)
    iterator = iter(frames)
    current = [None]
    seen = []
    auto = namespace['auto']

    def screenshot():
        try:
            current[0] = next(iterator)
            seen.append(current[0])
            return True
        except StopIteration:
            instance.running = False
            return None

    def find(name, **kwargs):
        if name.endswith('dead_all.png') and current[0] == 'defeat':
            if kwargs.get('find_type') == 'image_with_multiple_targets':
                return [(939, 395), (939, 532), (939, 670)]
            return (939, 532)
        if name.endswith('pause_assets.png'):
            return current[0] == 'playing'
        if name.endswith(('more_information_assets.png', 'gear_left.png', 'gear_right.png')):
            return current[0] == 'planning'
        return False

    auto.take_screenshot.side_effect = screenshot
    auto.find_element.side_effect = find
    auto.get_restore_time.return_value = None
    monkeypatch.setitem(sys.modules, 'tasks.base.retry', SimpleNamespace(check_times=lambda *a, **kw: False))
    result = instance.fight(defense_for_solo_state=defense_state)
    return instance, auto, seen, result


def test_same_defeat_dialog_does_not_exhaust_five_battle_retries(monkeypatch):
    battle, _, seen, _ = replay(monkeypatch, ['defeat'] * 8)
    assert battle.fail_times == 1
    assert len(seen) == 8


@pytest.mark.parametrize('resumed', ['playing', 'planning'])
def test_distinct_defeats_still_reach_retry_limit(monkeypatch, resumed):
    battle, _, _, result = replay(monkeypatch, ['defeat', 'defeat', resumed] * 5)
    assert battle.fail_times == 5
    assert result is False


def test_retry_click_uses_option_interior_instead_of_template_border(monkeypatch):
    _, auto, _, _ = replay(monkeypatch, ['defeat'])
    x, y = auto.mouse_click.call_args_list[0].args
    assert 1000 < x < 1400
    assert y == 532


def test_retry_restores_defense_budget_from_start_of_this_battle(monkeypatch):
    state = SimpleNamespace(remaining_turns=2)
    battle, _, _, _ = replay(monkeypatch, ['planning', 'playing', 'defeat', 'defeat', 'planning'], state)
    assert battle.operation_budgets == [2, 2]
    assert state.remaining_turns == 1
