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


def test_loop_limits_fallback_to_active_telegram_turn():
    loop = (ROOT / "src" / "loop.metta").read_text()

    assert "(or $msgnew (get-state &telegramFollowup))" in loop
    assert "(telegram.send_plaintext_reply $respi)" in loop


def test_telegram_loop_invokes_model_only_for_input_or_tool_followup():
    loop = (ROOT / "src" / "loop.metta").read_text()

    inference_gate = """(and (> (get-state &loops) 0)
                                     (or (!= (commchannel) telegram)
                                         (or $msgnew (get-state &telegramFollowup))))"""
    assert inference_gate in loop
    assert loop.index(inference_gate) < loop.index("(lib_llm_ext.callProvider")


def test_telegram_context_is_exposed_only_during_tool_followup():
    loop = (ROOT / "src" / "loop.metta").read_text()

    guard = '(and (== (commchannel) telegram) (not (get-state &telegramFollowup)))'
    assert loop.count(guard) == 2
    assert "(helper.response_needs_followup $respi)" in loop


def test_prompt_requires_immediate_response_without_forced_cycles():
    prompts = list((ROOT / "memory").glob("prompt*.txt"))

    assert prompts
    for prompt_path in prompts:
        prompt = prompt_path.read_text()
        assert "Handle each new human message immediately" in prompt
        assert "Take at least 5 agent cycles" not in prompt
        assert "ALWAYS query before responding" not in prompt
