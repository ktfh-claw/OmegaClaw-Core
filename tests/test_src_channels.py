import channels

class TestCommChannel(channels.CommChannel):

    started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        raise NotImplementedError()

    def receive(self) -> str:
        raise NotImplementedError()

    def send(self, message: str) -> None:
        raise NotImplementedError()


def test_commchannel_config():
    channel = TestCommChannel()
    channels.registerCommChannel("Test", channel)
    channels.commChannelStart("Test")
    assert channel.started


class ReceiveChannel(channels.CommChannel):
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def receive(self):
        if self.error is not None:
            raise self.error
        return self.result


def test_commchannel_receive_preserves_valid_string(monkeypatch):
    monkeypatch.setattr(channels, "_commchannel", ReceiveChannel("one | two"))
    monkeypatch.setattr(channels, "handle_control_message", lambda _message: False)

    assert channels.commChannelReceive() == "one | two"


def test_commchannel_receive_non_string_is_no_input(monkeypatch, caplog):
    monkeypatch.setattr(channels, "_commchannel", ReceiveChannel(None))

    assert channels.commChannelReceive() == ""
    assert "returned NoneType instead of str" in caplog.text


def test_commchannel_receive_exception_is_no_input(monkeypatch, caplog):
    monkeypatch.setattr(
        channels, "_commchannel", ReceiveChannel(error=RuntimeError("poll race"))
    )

    assert channels.commChannelReceive() == ""
    assert "receive failed; treating it as no input" in caplog.text


def test_loop_receive_uses_ground_sentinel_for_no_input(monkeypatch):
    monkeypatch.setattr(channels, "_commchannel", ReceiveChannel(None))

    assert channels.commChannelReceiveForLoop() == channels._NO_CHANNEL_INPUT


def test_loop_receive_preserves_message(monkeypatch):
    monkeypatch.setattr(channels, "_commchannel", ReceiveChannel("hello"))
    monkeypatch.setattr(channels, "handle_control_message", lambda _message: False)

    assert channels.commChannelReceiveForLoop() == "hello"


def test_loop_message_newness_is_total():
    sentinel = channels._NO_CHANNEL_INPUT

    assert channels.loopMessageIsNew(sentinel, sentinel) is False
    assert channels.loopMessageIsNew("hello", sentinel) is True
    assert channels.loopMessageIsNew("hello", "hello") is False
