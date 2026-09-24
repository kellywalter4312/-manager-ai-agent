"""Ambient wake-word listening - the production entry point.

    python scripts/ambient_voice.py

Says "Alfred" (or whatever IDENTITY.md's own Voice Activation phrases
declare) to a working microphone, waits for a request, answers it -
spoken, through the exact same voice path scripts/control_center.py and
scripts/serve_ui.py already use - and returns to waiting.

**Reuses the existing composition root and the existing activation
session; builds nothing new.** This file's entire job is: build the
system the one way this project ever builds it
(scripts/control_center.py's own build()), wrap the resulting Channel in
a VoiceActivationSession keyed on Alfred's own declared
VoiceActivation, and hand both to
src.channels.activation.run_ambient_loop - the loop that does the
actual listening. There is no second Manager, no second Brain, no
second voice path here.

Requires a real speech engine to be configured (MANAGER_SPEECH=windows
is the free, local, offline choice this project ships; see
src/providers/windows_speech_provider.py). Exits with a clear message,
not a hang, when none is configured or the engine reports a genuine
device/permission failure - see MicrophoneUnavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.channels.activation import MicrophoneUnavailable, VoiceActivationSession, run_ambient_loop  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402
from scripts.control_center import build  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    runtime, manager, channel, _conversation = build(settings)

    if getattr(channel, "speech", None) is None or channel.speech.name == "none":
        print(
            "No speech engine is configured (MANAGER_SPEECH is unset or 'none'). "
            "Set MANAGER_SPEECH=windows for free, local voice - see "
            "src/providers/speech_factory.py."
        )
        return 1

    activation = manager.own_skills.activation if manager.own_skills is not None else None
    if activation is None:
        speaker = manager.own_skills.name if manager.own_skills is not None else "the Manager"
        print(f"{speaker} has no declared voice activation phrase to listen for.")
        return 1

    session = VoiceActivationSession(channel, activation, presence=runtime.presence_registry)
    print(f"Listening for '{activation.key}' activation phrases. Ctrl+C to stop.")
    try:
        run_ambient_loop(session)
    except MicrophoneUnavailable as exc:
        print(f"Cannot listen: {exc}")
        return 1
    except KeyboardInterrupt:
        session.stop()
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
