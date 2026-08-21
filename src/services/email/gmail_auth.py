"""Gmail credentials: loading, refreshing, and building an API service.

Kept separate from gmail_mailbox.py on purpose. Authentication and message
fetching are different jobs with different failure modes, and separating them
means the fetching logic can be tested with an injected fake service while none
of this module runs at all.

Three safety properties are built into this file:

1. **Read-only scope.** The only scope requested is gmail.readonly. The
   resulting token is incapable of sending, deleting, or modifying anything -
   that restriction is enforced by Google, not by our code, so no bug or
   prompt on our side can circumvent it.

2. **Nothing here starts an interactive flow.** ``load_credentials`` never
   opens a browser: if there is no usable token it raises with instructions.
   Only ``authorize()`` is interactive, and only the standalone script calls
   it. Normal operation therefore cannot pop a consent screen.

3. **A token with broader scopes is refused.** If token.json was ever minted
   with write access, ``load_credentials`` rejects it rather than quietly
   using a more powerful credential than this milestone allows.

Google libraries are imported lazily so the project runs - and its whole test
suite passes - without them installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.services.email.mailbox import MailboxError
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The only scope this project requests. Read-only: no send, no delete, no
# modify, no label changes.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES: tuple[str, ...] = (GMAIL_READONLY_SCOPE,)

# Defaults live under data/, which is already gitignored.
DEFAULT_CREDENTIALS_PATH = "data/credentials.json"
DEFAULT_TOKEN_PATH = "data/token.json"

_SETUP_HINT = (
    "Run 'python scripts/authorize_gmail.py' once to grant read-only access. "
    "It needs an OAuth client file (credentials.json) downloaded from the "
    "Google Cloud console."
)


class GmailAuthError(MailboxError):
    """Raised when Gmail credentials are missing, invalid, or over-scoped.

    Subclasses MailboxError so callers that already handle mailbox failures -
    EmailAgent does - treat an auth problem as an ordinary failed task rather
    than an unhandled crash.
    """


def _require_google_packages() -> tuple[Any, Any, Any]:
    """Import the Google libraries, with an actionable error if absent."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GmailAuthError(
            "Gmail support requires the Google API packages. Install them with: "
            "pip install google-api-python-client google-auth-oauthlib"
        ) from exc
    return Credentials, Request, build


def load_credentials(
    token_path: str | Path = DEFAULT_TOKEN_PATH,
    credentials_path: str | Path = DEFAULT_CREDENTIALS_PATH,
) -> Any:
    """Load stored credentials, refreshing them if expired.

    Never interactive. A missing or unusable token raises GmailAuthError with
    instructions rather than silently opening a browser - background code must
    not be able to demand a human at the keyboard.
    """
    Credentials, Request, _ = _require_google_packages()

    token_file = Path(token_path)
    if not token_file.exists():
        raise GmailAuthError(
            f"No Gmail token found at '{token_file}'. {_SETUP_HINT}"
        )

    try:
        credentials = Credentials.from_authorized_user_file(str(token_file), list(SCOPES))
    except Exception as exc:  # noqa: BLE001 - any parse failure is an auth failure
        # The path is named; the file contents never are.
        raise GmailAuthError(f"Could not read Gmail token at '{token_file}': {exc}") from exc

    _reject_extra_scopes(credentials, token_file)

    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            logger.info("refreshing expired Gmail token")
            try:
                credentials.refresh(Request())
            except Exception as exc:  # noqa: BLE001
                raise GmailAuthError(
                    f"Could not refresh the Gmail token at '{token_file}': {exc}. {_SETUP_HINT}"
                ) from exc
            _write_token(credentials, token_file)
        else:
            raise GmailAuthError(
                f"The Gmail token at '{token_file}' is invalid and cannot be "
                f"refreshed. {_SETUP_HINT}"
            )

    return credentials


def _reject_extra_scopes(credentials: Any, token_file: Path) -> None:
    """Refuse a token that carries more than read-only access.

    Least privilege, verified rather than assumed: if a token was ever minted
    with send or modify scopes, this milestone declines to use it.
    """
    granted = getattr(credentials, "scopes", None)
    if not granted:
        return
    extra = set(granted) - set(SCOPES)
    if extra:
        raise GmailAuthError(
            f"The Gmail token at '{token_file}' grants more than read-only "
            f"access ({', '.join(sorted(extra))}). This milestone is read-only; "
            f"delete the token and re-authorize."
        )


def _write_token(credentials: Any, token_file: Path) -> None:
    """Persist refreshed credentials. Contents are never logged."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    logger.debug("stored refreshed Gmail token at '%s'", token_file)


def build_gmail_service(credentials: Any) -> Any:
    """Build the Gmail API client from credentials."""
    _, _, build = _require_google_packages()
    # cache_discovery=False avoids a noisy warning and a stale on-disk cache.
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def authorize(
    credentials_path: str | Path = DEFAULT_CREDENTIALS_PATH,
    token_path: str | Path = DEFAULT_TOKEN_PATH,
) -> Any:
    """Run the one-time interactive consent flow and store the token.

    INTERACTIVE: opens a browser and requires a human. Called only by
    scripts/authorize_gmail.py, never from the running system.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GmailAuthError(
            "Authorization requires google-auth-oauthlib. Install it with: "
            "pip install google-api-python-client google-auth-oauthlib"
        ) from exc

    client_file = Path(credentials_path)
    if not client_file.exists():
        raise GmailAuthError(
            f"No OAuth client file at '{client_file}'. Download one from the "
            f"Google Cloud console (APIs & Services -> Credentials -> OAuth "
            f"client ID -> Desktop app) and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), list(SCOPES))
    credentials = flow.run_local_server(port=0)
    _write_token(credentials, Path(token_path))
    return credentials
