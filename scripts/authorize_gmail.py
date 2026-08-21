"""One-time Gmail authorization. RUN THIS YOURSELF - nothing else calls it.

    python scripts/authorize_gmail.py

It opens a browser, asks you to grant READ-ONLY access to your Gmail, and
writes data/token.json. After that the application refreshes the token
silently and never needs a browser again.

Before running, complete the Google Cloud setup once:

  1. Create a project at https://console.cloud.google.com
  2. APIs & Services -> Library -> enable "Gmail API"
  3. APIs & Services -> OAuth consent screen -> configure (External is fine;
     add your own address as a test user)
  4. APIs & Services -> Credentials -> Create credentials
     -> OAuth client ID -> Application type: "Desktop app"
  5. Download the JSON and save it as data/credentials.json
  6. pip install google-api-python-client google-auth-oauthlib
  7. Run this script

The only scope requested is gmail.readonly. The resulting token cannot send,
reply to, delete, modify, or label anything - Google enforces that, not this
code. Both data/credentials.json and data/token.json are gitignored; never
commit or share them.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Settings  # noqa: E402
from src.services.email.gmail_auth import (  # noqa: E402
    GMAIL_READONLY_SCOPE,
    GmailAuthError,
    authorize,
)


def main() -> int:
    settings = Settings.from_env()
    credentials_path = Path(settings.gmail_credentials_path)
    token_path = Path(settings.gmail_token_path)

    print("Gmail authorization (read-only)")
    print(f"  scope       : {GMAIL_READONLY_SCOPE}")
    print(f"  client file : {credentials_path}")
    print(f"  token file  : {token_path}")
    print()

    if not credentials_path.exists():
        print(f"ERROR: no OAuth client file at '{credentials_path}'.")
        print("See the setup steps in the docstring at the top of this script.")
        return 1

    if token_path.exists():
        answer = input(f"'{token_path}' already exists. Replace it? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled. Existing token left untouched.")
            return 0

    print("A browser window will open. Grant read-only access to continue.")
    try:
        authorize(credentials_path=credentials_path, token_path=token_path)
    except GmailAuthError as exc:
        print(f"ERROR: {exc}")
        return 1

    print()
    print(f"Success. Token written to '{token_path}'.")
    print("Run the demo against your real inbox with:")
    print("    MANAGER_MAILBOX=gmail python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
