import hashlib
import logging

logger = logging.getLogger(__name__)

_commChannelRegistry = {}
_NO_CHANNEL_INPUT = "__OMEGA_NO_CHANNEL_INPUT__"


def _authenticated_export_principal() -> str | None:
    if _commchannel_id == "websocket":
        from config import config_get_by_key

        token = str(config_get_by_key("WS_TOKEN", "")).strip()
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"websocket:{digest}"

    from auth import get_channel_authenticated_user_id, is_auth_enabled

    if not is_auth_enabled():
        return None
    return get_channel_authenticated_user_id(_commchannel_id.upper())


def handle_control_message(message: str) -> bool:
    from src.memory_export import handle_export_command, is_export_command

    _, separator, command = message.rpartition(": ")
    if not separator:
        command = message
    if not is_export_command(command):
        return False

    try:
        authenticated_principal = _authenticated_export_principal()
    except Exception as exc:
        logger.exception("Failed to resolve memory-export principal: %s", exc)
        authenticated_principal = None

    reply = handle_export_command(command, authenticated_principal)
    if reply is not None:
        try:
            _commchannel.send(reply)
        except Exception as exc:
            logger.exception("Failed to deliver control-message response: %s", exc)
    return True


class CommChannel:
    """Communication channel implementation"""

    def start(self) -> None:
        """Configure and start communication channel"""
        pass

    def stop(self) -> None:
        """Stop communication channel and free resources"""
        pass

    def receive(self) -> str:
        """Receive message from the communication channel"""
        raise NotImplementedError()

    def send(self, message: str) -> None:
        """Send message via the communication channel"""
        raise NotImplementedError()

def registerCommChannel(id: str, channel: CommChannel) -> None:
    """
    Register communication channel in the registry.

    Arguments:
    id: the identifier of the plugin which is used to load it
    channel: the implementation of the channel
    """
    global _commChannelRegistry
    logger.info(f"registerCommChannel: registering communication channel {id}")
    _commChannelRegistry[id] = channel

_commchannel: CommChannel = None
_commchannel_id = ""

def commChannelStart(commchannel):
    """Select and start one of the communication channels registered by
    plugins"""
    global _commchannel, _commchannel_id
    _commchannel = _commChannelRegistry.get(commchannel, None)
    if _commchannel is None:
        error = f"commChannelStart: Communication channel plugin {commchannel} is not registered"
        logger.error(error)
        raise RuntimeError(error)
    _commchannel_id = str(commchannel).lower()
    _commchannel.start()

def commChannelReceive():
    """Receive message from selected communication channel"""
    global _commchannel
    try:
        received = _commchannel.receive()
    except Exception:
        logger.exception("Communication channel receive failed; treating it as no input")
        return ""

    if not isinstance(received, str):
        logger.warning(
            "Communication channel receive returned %s instead of str; treating it as no input",
            type(received).__name__,
        )
        return ""

    messages = received.split(" | ")
    return " | ".join(
        message for message in messages if not handle_control_message(message)
    )


def commChannelReceiveForLoop():
    """Return a non-empty, ground string for the MeTTa polling loop.

    PeTTa's ``repr`` reduction can leave its result unbound for an empty Python
    string.  Keep the public channel API's empty-string semantics while using a
    stable sentinel at the Python/MeTTa boundary.
    """
    message = commChannelReceive()
    return message if message else _NO_CHANNEL_INPUT


def loopMessageIsNew(message, previous):
    """Return a concrete bool without exposing MeTTa to partial comparisons."""
    return message != _NO_CHANNEL_INPUT and message != previous

def commChannelSend(message):
    """Send message via selected communication channel"""
    global _commchannel
    _commchannel.send(message)
