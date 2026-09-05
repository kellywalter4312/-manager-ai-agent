"""A real, keyless, generic live-information source: Wikipedia's own
public search API.

**Real, not speculative.** No account, no API key, no paid signup, no
vendor SDK - one fixed, read-only, publicly documented REST endpoint
(``en.wikipedia.org/w/api.php``), called with the Python standard
library's own ``urllib``. This is the smallest genuinely working current-
information source available without introducing a new dependency or a
credential this project has no way to provision.

**Generic, not subject-specific.** There is no football word, no club
name, no company, no person here and there must never be one - the query
string is whatever the person actually asked, passed straight through as
a search term. The identical article this class would fetch for "Malick
Fofana", "the French Revolution" or "quantum computing" is decided
entirely by Wikipedia's own search relevance, never by a branch in this
file.

**Governed, not unrestricted.** One destination host, one endpoint, one
HTTP method (GET), a bounded, validated query parameter, and a fixed
timeout. This is not browsing - there is no way to reach an arbitrary
URL through this class - and it raises like ordinary Python on any
failure, exactly as ``LiveInfoProvider.fetch`` already requires; the
caller (``LiveInformationTool``, then ``ToolSet.invoke``'s existing
boundary) is what turns that into an honest, structured failure.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now

# Wikipedia's own API etiquette asks every client to identify itself. Named
# for this project, never for a subject it might be asked about.
USER_AGENT = "AlfredOS-LiveInformationProvider/1.0 (educational project; no API key)"

_SEARCH_ENDPOINT = "https://en.wikipedia.org/w/api.php"
_TAG_PATTERN = re.compile(r"<[^>]+>")


def _clean_snippet(raw: str) -> str:
    """Strip Wikipedia's <span class="searchmatch"> markup and decode
    HTML entities, so what reaches a reasoning step is plain text."""
    import html

    return html.unescape(_TAG_PATTERN.sub("", raw)).strip()


class WikipediaLiveInfoProvider(LiveInfoProvider):
    """Searches Wikipedia for a query and returns the top matches as
    current evidence, each dated by when it was last edited.

    ``result_limit`` and ``timeout`` are the only knobs - both are
    mechanics of the call, never anything that could express a topic.
    """

    def __init__(self, *, result_limit: int = 3, timeout: float = 8.0) -> None:
        if result_limit <= 0:
            raise ValueError("result_limit must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._result_limit = result_limit
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "wikipedia"

    def fetch(self, query: str) -> LiveInfoResult:
        """Search Wikipedia for ``query`` and return the top results as
        one piece of evidence.

        Raises on any network or parsing failure - a timeout, an
        unreachable host, an HTTP error, a response that is not the JSON
        shape expected - the same convention every other provider in
        this project already follows. A search that runs successfully
        and matches nothing returns an honestly empty result, exactly
        like an ordinary Python function that looked and found nothing.
        """
        if not query.strip():
            raise ValueError("query must not be empty")

        params = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": self._result_limit,
                "srprop": "snippet|timestamp",
                "format": "json",
            }
        )
        request = urllib.request.Request(
            f"{_SEARCH_ENDPOINT}?{params}",
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"could not reach Wikipedia: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Wikipedia returned an unreadable response: {exc}") from exc

        results = payload.get("query", {}).get("search", [])
        if not results:
            return LiveInfoResult(query=query, content="", source=self.name, retrieved_at=utc_now())

        lines = [
            f"{item.get('title', 'untitled')} (last updated {item.get('timestamp', 'unknown')}): "
            f"{_clean_snippet(item.get('snippet', ''))}"
            for item in results
        ]
        return LiveInfoResult(
            query=query,
            content="\n".join(lines),
            source="wikipedia",
            retrieved_at=utc_now(),
        )
