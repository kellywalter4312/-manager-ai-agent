"""A provider that needs no model, no key and no network.

This is the default, and it is not a placeholder to be replaced later - it is
how the system stays runnable and testable with zero external dependencies.
"Not configured" becomes a valid operating state rather than a crash.

It is also the honest default for this project: a Claude Pro subscription is
not an API key, and nothing here assumes API access exists.
"""

from __future__ import annotations

from src.providers.base import Completion, LLMProvider


class NullProvider(LLMProvider):
    """Returns a fixed, clearly-labelled response instead of calling a model.

    Two properties are deliberate:

    - **Deterministic.** The same prompt always produces the same output, so
      tests that depend on it cannot flake.
    - **Never echoes the prompt.** Completions frequently end up in logs, and a
      prompt may carry task content that should not be duplicated there. The
      response reports the prompt's *size*, not its contents.
    """

    @property
    def name(self) -> str:
        return "null"

    @property
    def produces_answers(self) -> bool:
        return False

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        return Completion(
            text=(
                "[null-provider] no language model is configured; "
                f"received a {len(prompt)}-character prompt"
            ),
            provider=self.name,
            # No model and no token counts: there is nothing to report, and
            # inventing plausible-looking numbers would be worse than None.
        )
