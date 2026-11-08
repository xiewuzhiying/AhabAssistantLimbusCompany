"""Exercise defense submission across delayed game UI transitions."""

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def load_battle(auto):
    tree = ast.parse(Path('tasks/battle/battle.py').read_text())
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name in {'Battle', 'DefenseForSoloState'}]
    battle = next(n for n in classes if n.name == 'Battle')
    battle.body = [n for n in battle.body if isinstance(n, ast.FunctionDef)
                   and n.name in {'__init__', '_battle_operation',
                                  '_submit_selected_defense', '_confirm_defense_started'}]
    namespace = {
        '__name__': __name__, 'dataclass': dataclass,
        'DEFENSE_FOR_SOLO_TURN_LIMIT': 5,
        'auto': auto, 'sleep': lambda _: None,
        'log': Mock(), 'cfg': SimpleNamespace(set_win_size=1080),
    }
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *classes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'tasks/battle/battle.py', 'exec'), namespace)
    instance = namespace['Battle']()
    instance._defense_this_round = Mock(return_value=True)
    return instance, namespace['DefenseForSoloState']


class BattleUI:
    """Selection remains visible until the game accepts a submit attempt."""

    def __init__(self, accepted_after=2):
        self.accepted_after = accepted_after
        self.enters = 0
        self.playing = False
        self.keys = []

    def mouse_click_blank(self):
        pass

    def find_element(self, name, **kwargs):
        if name.endswith('pause_assets.png'):
            return self.playing
        if name.endswith(('gear_left.png', 'gear_right.png')):
            return not self.playing
        return False

    def key_press(self, key):
        self.keys.append(key)
        if key == 'enter':
            self.enters += 1
            self.playing = self.enters >= self.accepted_after


def operate(battle, state=None, *, first=False, used=False):
    return battle._battle_operation(
        first_turn=first, defense_first_round=first, avoid_skill_3=False,
        defense_for_solo_state=state, defense_for_solo_used_this_turn=used,
    )


def test_last_defense_waits_for_submission_without_win_rate_fallback():
    ui = BattleUI(accepted_after=2)
    battle, state_type = load_battle(ui)
    state = state_type(remaining_turns=1)

    assert operate(battle, state) is False
    assert state.remaining_turns == 1
    assert ui.keys == ['enter']

    # The next loop retries submission, not portrait toggles or skill selection.
    assert operate(battle, state) is True
    assert state.remaining_turns == 0
    assert ui.keys == ['enter', 'enter']
    battle._defense_this_round.assert_called_once()

    operate(battle, state, used=True)
    assert state.remaining_turns == 0
    assert 'p' not in ui.keys

    # A later planning turn can resume the ordinary policy.
    ui.playing = False
    operate(battle, state)
    assert ui.keys[-2:] == ['p', 'enter']


@pytest.mark.parametrize('mode', ['limited', 'first', 'all'])
def test_failed_defense_selection_never_falls_back_to_attack(mode):
    ui = BattleUI()
    battle, state_type = load_battle(ui)
    state = state_type() if mode == 'limited' else None
    battle.defense_all_time = mode == 'all'
    battle._defense_this_round.return_value = False

    assert operate(battle, state, first=mode == 'first') is False
    assert ui.keys == []
    if state:
        assert state.remaining_turns == 5


@pytest.mark.parametrize('mode', ['first', 'all'])
def test_other_defense_modes_preserve_selection_while_submission_is_delayed(mode):
    ui = BattleUI()
    battle, _ = load_battle(ui)
    battle.defense_all_time = mode == 'all'

    operate(battle, first=mode == 'first')
    operate(battle, first=mode == 'first')
    assert ui.keys == ['enter', 'enter']
    battle._defense_this_round.assert_called_once()


def test_defense_that_starts_during_drag_is_counted_once_without_more_input():
    ui = BattleUI()
    battle, state_type = load_battle(ui)
    state = state_type(remaining_turns=2)
    def select_and_start():
        ui.playing = True
        return True
    battle._defense_this_round.side_effect = select_and_start

    assert operate(battle, state) is True
    assert state.remaining_turns == 1
    assert ui.keys == []


def test_later_observed_playback_confirms_pending_defense_only_once():
    ui = BattleUI(accepted_after=10)
    battle, state_type = load_battle(ui)
    state = state_type()
    operate(battle, state)
    assert state.remaining_turns == 5

    # fight() may observe playback after _battle_operation has returned.
    ui.playing = True
    assert battle._confirm_defense_started() is True
    assert battle._confirm_defense_started() is False
    assert state.remaining_turns == 4


def test_defense_intent_survives_a_missed_planning_control():
    ui = BattleUI()
    ui.find_element = lambda *_args, **_kwargs: False
    battle, state_type = load_battle(ui)
    state = state_type()
    assert operate(battle, state) is False
    assert ui.keys == []
    assert state.remaining_turns == 5


def test_next_planning_turn_can_defend_after_playback_was_already_confirmed():
    ui = BattleUI(accepted_after=1)
    battle, state_type = load_battle(ui)
    state = state_type(remaining_turns=2)
    assert operate(battle, state) is True
    assert state.remaining_turns == 1
    # Playback can finish while the caller is paused; its old latch is stale.
    ui.playing = False
    assert operate(battle, state, used=True) is True
    assert state.remaining_turns == 0
    assert ui.keys == ['enter', 'enter']
