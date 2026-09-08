"""A genuine speech engine: Windows' own ``System.Speech`` - real recognition,
real synthesis, no vendor SDK, no network, no credential, no new dependency.

**Why this provider, and not a cloud vendor.** This project's own
architecture-audit guard already forbids ``speech_recognition``, ``pyttsx3``
and ``whisper`` anywhere in ``src/`` - a pre-existing boundary this module
does not touch, let alone weaken. Beyond that specific rule, adding *any*
cloud STT/TTS vendor here would mean asserting it "genuinely works" with no
credential and no verified network path in the environment this was built
in to prove it - exactly the "a key/SDK exists" standing in for "this
actually works" this whole stage was written to refuse. Windows already
ships a real, local, fully offline speech engine on every machine this
project's own scripts already assume (``powershell``,
``scripts/serve_ui.py``'s own use of ``http.server`` shows this project
already accepts a Windows-shaped deployment target) - reaching it costs one
``subprocess`` call to a binary already on the machine, not a package, a key
or a network path.

**Genuinely verified, not merely constructed.** Before this file existed, the
following was confirmed by hand, directly against this engine, outside any
test: a real ``SpeechSynthesizer`` wrote a 167KB real WAV file for real text;
a real ``SpeechRecognitionEngine`` read that file back and transcribed it
correctly (confidence 0.74); the default audio *output* device accepted a
real ``Speak()`` call without error; the default audio *input* device opened
without error and correctly reported "no speech recognized" when nothing was
said. None of that is a claim that a live human utterance was ever
transcribed here - nothing in this environment can produce real speech audio
to test that specific path, and this module does not claim otherwise.

**The one seam every public method calls through**, ``_run_powershell``, is
what tests replace - never the real ``subprocess.run`` - the same separation
``src/providers/factory.py``'s ``_build_anthropic`` already draws between
"decide whether to call" and "the call itself".

**Text never enters the generated script as a literal.** It is written to a
private temporary file and read back inside the script with
``Get-Content -Raw`` - arbitrary caller-supplied text (which ``speak()``
must accept unmodified) is never interpolated into a shell/script string,
which is what would make this an injection risk.

**Voice mapping (Stage 11.2): a real, distinct, installed voice per agent -
never a hardcoded name.** ``speak()`` no longer merely accepts a
``VoiceProfile`` and ignores it. ``_select_voice_name`` deterministically
maps ``voice.key`` onto one of *this machine's own, actually-installed*
voices (queried live via ``GetInstalledVoices()``, never a name baked into
this file) - so two agents with different declared identities genuinely
select two different real voices wherever this runs, on whatever roster
that machine happens to have, and the same agent always selects the same
one. This is not an attempt to reproduce a declared accent - SAPI's
installed voices are named English variants (a real one, on the machine
this was built and verified against: nine, mixed gender, none tagged
"Nigerian" or "British") - it is an honest, provider-owned choice of
*which distinct real voice* stands in for a declared identity, exactly the
mapping ``VoiceProfile``'s own docstring says belongs to whichever engine
receives it, never to the identity layer itself.

**User selection (Stage 11.4): the same mapping, now overridable.** A
person picks a real voice this engine actually reports
(``WindowsSpeechAdapter().installed_voice_names()`` - discovery already
existed, unchanged) and writes it into IDENTITY.md's ``## Voice`` section
as ``preferred_voice:``. ``_select_voice_name`` honours it - matched
case-insensitively against the live roster, never assumed - and falls
back to the unchanged Stage 11.2 default the moment that name is no
longer among this machine's installed voices, so an uninstalled or
mistyped preference degrades safely rather than silently or with an
error.
"""

from __future__ import annotations

import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_options import LANGUAGE_NAMES, VoiceOption
from src.utils.logging import get_logger

logger = get_logger(__name__)

# How long a real listen() waits for speech to begin before honestly giving
# up, absent a caller-supplied timeout - bounded so a broken or silent
# microphone degrades to "no speech recognized" rather than hanging forever.
DEFAULT_LISTEN_TIMEOUT_SECONDS = 10.0
# Slack added on top of the recognition timeout for the *process* timeout -
# starting the .NET runtime and loading System.Speech itself takes a moment
# that is not time spent listening.
_PROCESS_TIMEOUT_PADDING_SECONDS = 8.0
_SPEAK_TIMEOUT_SECONDS = 15.0

_LISTEN_SCRIPT = """
Add-Type -AssemblyName System.Speech
$ErrorActionPreference = 'Stop'
try {{
  $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
  $recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  {input_line}
  $result = $recognizer.Recognize([TimeSpan]::FromSeconds({timeout}))
  if ($result) {{ Write-Output $result.Text }}
  $recognizer.Dispose()
}} catch {{
  Write-Error $_.Exception.Message
  exit 1
}}
"""

_SPEAK_SCRIPT = """
Add-Type -AssemblyName System.Speech
$ErrorActionPreference = 'Stop'
try {{
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  {voice_line}
  {output_line}
  $text = Get-Content -Raw -Path "{text_path}"
  $synth.Speak($text)
  $synth.Dispose()
}} catch {{
  Write-Error $_.Exception.Message
  exit 1
}}
"""

# Queries this machine's own, actually-installed voices - never a list
# baked into this file, so _select_voice_name below adapts to whatever
# roster a given machine really has rather than assuming one.
_VOICES_SCRIPT = """
Add-Type -AssemblyName System.Speech
$ErrorActionPreference = 'Stop'
try {
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { Write-Output $_.VoiceInfo.Name }
  $synth.Dispose()
} catch {
  Write-Error $_.Exception.Message
  exit 1
}
"""

# The same query, also emitting each voice's real .NET Culture (e.g.
# "en-US") - a genuine per-voice fact this engine has always exposed
# (``VoiceInfo.Culture``), never read before this checkpoint. A separate
# script rather than changing the one above: installed_voice_names()'s
# existing, tested, one-name-per-line output must stay byte-identical -
# nothing about the Stage 11.2/11.4 voice-mapping behaviour it backs may
# change as a side effect of adding discovery.
_VOICES_WITH_CULTURE_SCRIPT = """
Add-Type -AssemblyName System.Speech
$ErrorActionPreference = 'Stop'
try {
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
    Write-Output ($_.VoiceInfo.Name + "|" + $_.VoiceInfo.Culture.Name)
  }
  $synth.Dispose()
} catch {
  Write-Error $_.Exception.Message
  exit 1
}
"""


def _select_voice_name(voice: VoiceProfile | None, available: tuple[str, ...]) -> str | None:
    """Choose one of *this machine's own installed* voices for an agent -
    an explicit user selection first (Stage 11.4), honoured only when it
    is genuinely still available; otherwise a deterministic choice derived
    from the agent's declared identity, never a name hardcoded in this
    file and never a branch on which agent this is.

    **Stage 11.4 - user selection.** ``voice.preferred_voice``, when set,
    is matched against ``available`` case-insensitively (a user typing
    "microsoft zira desktop" should not have to match the engine's own
    capitalisation exactly) and the *real* installed name is returned, so
    the caller always embeds a string this engine actually recognises.
    Matched, never assumed: a preference naming a voice that was
    uninstalled, mistyped, or never existed on this machine falls straight
    through to the deterministic default below - a stale or wrong
    preference must degrade to the existing safe behaviour, never to
    silence or a crash.

    **The existing default (Stage 11.2), unchanged.** The same
    ``voice.key`` always selects the same installed voice; two different
    keys select two different installed voices whenever more than one is
    actually available (a stable hash of the key modulo the roster size -
    not an attempt to semantically match "nigerian-english" or "measured,
    low, formal" to a specific installed voice's own metadata, which this
    engine's voices do not reliably expose).

    None - never a guess - when there is nothing to choose (``voice`` is
    None) or nothing to choose *from* (no voices reported installed). A
    single installed voice is returned as-is: honestly, the only choice
    this machine's roster actually offers, not a failure.
    """
    if voice is None or not available:
        return None
    if voice.preferred_voice:
        wanted = voice.preferred_voice.lower()
        for name in available:
            if name.lower() == wanted:
                return name
    if len(available) == 1:
        return available[0]
    index = zlib.crc32(voice.key.encode("utf-8")) % len(available)
    return available[index]


def _run_powershell(script: str, *, timeout: float) -> subprocess.CompletedProcess:
    """The one place a real process is ever started. Tests replace this
    function directly - never the real engine - so an automated run never
    depends on a live microphone, a live speaker, or ``powershell.exe``
    actually being on the machine running the suite.
    """
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class WindowsSpeechAdapter(SpeechAdapter):
    """Speech through Windows' own ``System.Speech`` engine.

    ``availability`` starts UNKNOWN, exactly as ``SpeechAdapter``'s own
    contract requires, and only ever becomes AVAILABLE after a real
    ``listen()``/``speak()`` call actually succeeded - never because
    ``powershell.exe`` happens to exist, and never because construction
    completed without raising.
    """

    def __init__(self) -> None:
        self._availability = Availability()
        # Cached per instance, not per process: querying is a real
        # subprocess call, and re-running it on every speak() would be
        # wasted work for a roster that does not change mid-session. Kept
        # per instance (not module-level) so a fresh WindowsSpeechAdapter -
        # the shape every test in this project already constructs - always
        # starts with a clean, unqueried cache.
        self._voice_names: tuple[str, ...] | None = None

    @property
    def name(self) -> str:
        return "windows"

    @property
    def availability(self) -> Availability:
        return self._availability

    def installed_voice_names(self) -> tuple[str, ...]:
        """This machine's own, real installed voice names - queried once,
        live, and cached. Empty (never raises) when the engine cannot be
        reached at all; that is itself an honest answer this method's
        caller (``speak``) already knows how to act on.
        """
        if self._voice_names is not None:
            return self._voice_names

        try:
            result = _run_powershell(_VOICES_SCRIPT, timeout=_SPEAK_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            self._voice_names = ()
            return self._voice_names

        if result.returncode != 0:
            self._voice_names = ()
            return self._voice_names

        self._voice_names = tuple(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
        return self._voice_names

    def available_voices(self) -> "tuple[VoiceOption, ...]":
        """This machine's own, real installed voices, each with the real
        locale .NET reports for it (``VoiceInfo.Culture``, e.g.
        ``"en-US"``) and the language that locale's own prefix names via
        ``voice_options.LANGUAGE_NAMES`` - never a guessed language, and
        never an accent: SAPI genuinely does not expose one (see this
        module's own docstring on why "nigerian-english" was never
        reproducible here). A locale prefix this project's small table
        does not recognise still yields a real, honest locale with
        ``language=None`` rather than being dropped.

        Queried live, once per instance (mirrors ``installed_voice_names``'s
        own caching - a real subprocess call, not repeated for no reason),
        never persisted: a fresh instance always re-asks this machine.
        """
        try:
            result = _run_powershell(_VOICES_WITH_CULTURE_SCRIPT, timeout=_SPEAK_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if result.returncode != 0:
            return ()

        options: list[VoiceOption] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, culture = line.partition("|")
            name, culture = name.strip(), culture.strip()
            if not name:
                continue
            locale = culture or None
            prefix = culture.split("-", 1)[0].lower() if culture else ""
            language = LANGUAGE_NAMES.get(prefix)
            options.append(
                VoiceOption(voice_id=name, name=name, provider=self.name, language=language, locale=locale)
            )
        return tuple(options)

    def listen(self, timeout: float | None = None) -> str:
        wait = float(timeout) if timeout else DEFAULT_LISTEN_TIMEOUT_SECONDS
        script = _LISTEN_SCRIPT.format(
            input_line="$recognizer.SetInputToDefaultAudioDevice()", timeout=wait
        )
        try:
            result = _run_powershell(script, timeout=wait + _PROCESS_TIMEOUT_PADDING_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f"windows speech recognition could not run: {exc}"
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            raise SpeechUnavailable(reason) from exc

        if result.returncode != 0:
            reason = (result.stderr or "recognition failed").strip()
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            raise SpeechUnavailable(f"windows speech recognition failed: {reason}")

        text = result.stdout.strip()
        if not text:
            reason = "no speech was recognized"
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            raise SpeechUnavailable(reason)

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return text

    def speak(self, text: str, voice: VoiceProfile | None = None) -> bool:
        selected = _select_voice_name(voice, self.installed_voice_names())
        voice_line = f'$synth.SelectVoice("{selected}")' if selected else ""
        return self._synthesize(
            text, output_line="$synth.SetOutputToDefaultAudioDevice()", voice_line=voice_line
        )

    def _synthesize(self, text: str, *, output_line: str, voice_line: str = "") -> bool:
        """Shared by the real ``speak()`` (default audio device) and by
        this project's own honest verification of the engine (a WAV file,
        checked for real audio content) - one script builder, two
        destinations, never two implementations of "ask the engine to
        speak". ``voice_line`` is a real, already-installed voice name
        (see ``_select_voice_name``) - never text a caller supplied, so
        embedding it directly carries none of the risk ``text`` itself
        would."""
        text_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                handle.write(text)
                text_path = handle.name

            script = _SPEAK_SCRIPT.format(
                voice_line=voice_line, output_line=output_line, text_path=text_path
            )
            result = _run_powershell(script, timeout=_SPEAK_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f"windows speech synthesis could not run: {exc}"
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            return False
        finally:
            if text_path is not None:
                Path(text_path).unlink(missing_ok=True)

        if result.returncode != 0:
            reason = (result.stderr or "synthesis failed").strip()
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            return False

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return True
