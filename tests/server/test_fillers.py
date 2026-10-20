from agent.fillers import GENERIC, HOLD_ON, PHRASES, Fillers
from agent.tools import TOOLS


def test_one_filler_per_turn_matches_its_tools():
    fillers = Fillers()
    assert fillers.pick(1, ["get_account"]) in PHRASES["get_account"]
    # A follow-up tool call in the same turn stays quiet.
    assert fillers.pick(1, ["search_help_center"]) is None
    tickets = ["open_ticket", "open_ticket"]
    assert fillers.pick(2, tickets) in PHRASES["open_ticket"]
    assert fillers.pick(3, ["get_account", "search_help_center"]) in GENERIC
    assert fillers.pick(4, ["new_tool"]) in GENERIC


def test_every_tool_has_its_own_phrases():
    assert set(PHRASES) == {tool.name for tool in TOOLS}


def test_fillers_vary_without_repeating_back_to_back():
    fillers = Fillers()
    picks = [fillers.pick(generation, ["search_help_center"]) for generation in range(200)]
    assert all(previous != current for previous, current in zip(picks, picks[1:]))
    assert set(picks) == set(PHRASES["search_help_center"])
    # The reply waits for a filler that is still playing.
    phrases = [phrase for options in (*PHRASES.values(), GENERIC, HOLD_ON) for phrase in options]
    assert all(len(phrase.split()) <= 8 and phrase.isascii() for phrase in phrases)
