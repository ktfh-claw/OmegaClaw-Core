"""Focused tests for Telegram's non-command response fallback."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "channels"))

import telegram  # noqa: E402


def test_plaintext_response_is_relayed_verbatim(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message", sent.append)

    assert telegram.send_plaintext_reply("A normal answer\nwith details.") is True
    assert sent == ["A normal answer\nwith details."]


def test_empty_response_gets_delivery_safe_reply(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message", sent.append)

    assert telegram.send_plaintext_reply("  \n") is True
    assert sent == [telegram._EMPTY_REPLY]


def test_command_expression_is_never_relayed_by_fallback(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message", sent.append)

    assert telegram.send_plaintext_reply('(shell "unsafe")') is False
    assert telegram.send_plaintext_reply('shell "also unsafe"') is False
    assert telegram.send_plaintext_reply('send "already routed"') is False
    assert telegram.send_plaintext_reply('arc-read "http://172.17.0.1:18080"') is False
    assert telegram.send_plaintext_reply('arc-submit "url" "body"') is False
    assert sent == []


def test_loop_allows_structured_followup_or_opt_in_autonomous_wake():
    loop = (ROOT / "src" / "loop.metta").read_text()

    assert "(configure telegramAutonomousWake False)" in loop
    assert "(get-state &telegramFollowup)\n                                             (get-state &autonomousWake)" in loop
    assert "(llmResponseNeedsFollowup $response)" in loop
    assert "telegram.send_plaintext_reply" not in loop


def test_telegram_timer_arms_an_opt_in_autonomous_turn():
    loop = (ROOT / "src" / "loop.metta").read_text()

    config_guard = "(telegramAutonomousWake))"
    arm_wake = "(change-state! &autonomousWake True)"
    assert config_guard in loop
    assert arm_wake in loop
    assert loop.index(config_guard) < loop.index(arm_wake)


def test_telegram_autonomous_turn_has_history_and_resets_wake_flag():
    loop = (ROOT / "src" / "loop.metta").read_text()

    context_guard = "(not (or (get-state &telegramFollowup)\n                                           (get-state &autonomousWake)))"
    history_update = "(addToHistory $msg $sexpr $msgnew)"
    reset_wake = "(change-state! &autonomousWake False)"
    assert context_guard in loop
    assert loop.count(reset_wake) == 2
    assert loop.index(history_update) < loop.rindex(reset_wake)


def test_prompt_requires_immediate_response_without_forced_cycles():
    prompts = list((ROOT / "memory").glob("prompt*.txt"))

    assert prompts
    for prompt_path in prompts:
        prompt = prompt_path.read_text()
        assert "Handle each new human message immediately" in prompt
        assert "Take at least 5 agent cycles" not in prompt
        assert "ALWAYS query before responding" not in prompt
