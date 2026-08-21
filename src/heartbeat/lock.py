"""One heartbeat at a time.

The state file is written atomically, so a concurrent run cannot leave a
half-file behind. What atomicity does *not* prevent is the sequence that
actually loses data:

    process A   load state (31 messages)
    process B   load state (31 messages)
    process A   fetch, analyse, save (32 messages)
    process B   fetch, analyse, save (32 messages - A's addition is gone)

Both ran cleanly. Both wrote a valid file. One message was silently dropped and
one inbox was analysed twice, producing two near-identical reports. The window
is the whole cycle, not the write, so the lock has to cover the whole cycle.

Why a lock file created with O_CREAT|O_EXCL rather than msvcrt.locking or a
third-party package: exclusive creation is one atomic syscall on both Windows
and POSIX, it needs no dependency, and the artefact left behind after a crash
is an ordinary file that a person can read and delete. A held OS-level lock
vanishes when the process dies, which sounds better until you want to know
*why* last night's run never happened.

The crash story is the reason for ``stale_after``. A process killed mid-cycle
leaves its lock file on disk with nobody to remove it, and a lock that outlives
its owner forever would silently stop the heartbeat - trading a rare race for a
permanent outage. An abandoned lock is therefore reclaimed once it is older
than a full cycle could plausibly take.

Deliberately *not* used for liveness: the recorded pid. Checking whether a pid
is running is portable in neither syntax nor meaning - pids are reused, and a
stale pid can belong to an unrelated process. It is recorded because it is the
first thing anyone debugging a stuck lock wants to know, not because anything
here trusts it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from src.heartbeat.state import utc_now
from src.utils.logging import get_logger

logger = get_logger(__name__)

# How long a lock may sit untouched before it is treated as abandoned. An hour
# is comfortably longer than any real cycle - a fetch of 25 messages and a
# handful of analyzers is seconds - while still recovering well inside the
# default 60-minute schedule, so at most one wake is lost to a crashed run.
DEFAULT_STALE_AFTER = timedelta(hours=1)


class HeartbeatLock:
    """A cooperative, single-holder lock backed by an exclusively-created file.

    Cooperative on purpose: it guards the heartbeat against *itself*, which is
    the only writer that exists. It is not a general-purpose mutex and makes no
    attempt to stop an unrelated program from editing the state file.
    """

    def __init__(
        self,
        path: str | Path,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._path = Path(path)
        self._stale_after = stale_after
        self._clock = clock
        self._held = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        """Whether *this* instance currently holds the lock."""
        return self._held

    def acquire(self) -> bool:
        """Take the lock, or return False if another run holds it.

        Never blocks and never waits. A heartbeat that cannot start now is not
        worth queueing: the next wake is minutes away, and a queue of pending
        cycles is a thundering herd waiting for a slow run to finish.
        """
        if self._held:
            # Re-entering would mean a caller lost track of its own lock, and
            # returning True would let the eventual release fire twice.
            raise RuntimeError(f"lock at '{self._path}' is already held by this runner")

        if self._create():
            self._held = True
            return True

        if self._reclaim_if_stale() and self._create():
            self._held = True
            return True

        logger.info("another heartbeat holds the lock at '%s'; skipping this wake", self._path)
        return False

    def release(self) -> None:
        """Release a lock this instance holds. Safe to call more than once.

        Only removes the file when this instance took it, so a run that found
        the lock busy can never delete the holder's lock on its way out.
        """
        if not self._held:
            return

        self._held = False
        try:
            self._path.unlink()
        except FileNotFoundError:
            # Someone reclaimed it as stale while we were still working. The
            # cycle is finished either way; nothing is gained by complaining.
            logger.warning("lock file at '%s' vanished before release", self._path)
        except OSError as exc:
            # Leaving the file behind is survivable - it ages out - whereas
            # raising here would turn a completed cycle into a failed one.
            logger.warning("could not remove lock file at '%s': %s", self._path, exc)

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create(self) -> bool:
        """Create the lock file, or return False if it already exists.

        O_CREAT|O_EXCL is the whole mechanism: the check and the creation are
        one operation, so two processes arriving together cannot both succeed.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError as exc:
            # An unwritable lock directory must not stop the heartbeat from
            # running - it only removes the protection, which is where we were
            # before this module existed.
            logger.warning("could not create lock at '%s': %s; running unlocked", self._path, exc)
            return True

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"pid": os.getpid(), "acquired_at": self._clock().isoformat()},
                    handle,
                )
        except OSError as exc:
            logger.warning("lock at '%s' created but not written: %s", self._path, exc)
        return True

    def _reclaim_if_stale(self) -> bool:
        """Remove an abandoned lock. Returns whether one was removed."""
        age = self._age()
        # <= not <: a lock exactly at the limit has not yet outlived it.
        # "Older than" is strict, so the boundary favours the holder -
        # stealing a lock a moment early is worse than waiting one wake.
        if age is None or age <= self._stale_after:
            return False

        try:
            self._path.unlink()
        except OSError:
            return False

        logger.warning(
            "removed a stale heartbeat lock at '%s' (held for %s, limit %s); "
            "the run that created it did not finish",
            self._path, age, self._stale_after,
        )
        return True

    def _age(self) -> timedelta | None:
        """How long the lock has existed, by its recorded time then its mtime.

        The recorded timestamp is preferred because it is the lock's own claim
        about when it was taken. mtime is the fallback for a lock file that was
        created but never written - the narrow window inside _create.
        """
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            acquired = datetime.fromisoformat(str(raw["acquired_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            try:
                stamp = self._path.stat().st_mtime
            except OSError:
                return None
            acquired = datetime.fromtimestamp(stamp, tz=self._clock().tzinfo)

        now = self._clock()
        if acquired.tzinfo is None and now.tzinfo is not None:
            acquired = acquired.replace(tzinfo=now.tzinfo)
        return now - acquired
