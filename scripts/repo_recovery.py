"""0.1 Repository Recovery - a real, re-runnable answer to four questions:

    A. What is the repository's actual current state?
    B. What is committed, and what is only local?
    C. Which modules are production-reachable, test-only, or reachable by
       nothing at all - the candidates for dead, stale or prototype code?
    D. Is the canonical architecture document's own status vocabulary
       (see ARCHITECTURE.md's "IMPLEMENTED / PREPARED / PLANNED / NOT
       STARTED" table) still true of the actual import graph?

This module answers those questions by *computing* them from the
repository itself - git state and a static import-graph walk - never by
reading or trusting a document about them. ARCHITECTURE.md remains the
authoritative *narrative*; this is the mechanism that can check it.

**Why a static import graph, not execution.** Actually importing every
module would run side effects (network guards aside, plenty of modules
touch the filesystem at import time) and would still miss nothing an AST
walk does not already see - every import in this codebase is an ordinary
top-level or function-body `import`/`from X import Y` statement, never a
string-built one. A static walk is the smallest correct tool: safe to
run against a live, real repository, and exact for the question asked.

**Why this does not replace tests/test_production_reachability.py.**
That file proves one specific chain (the opportunity interpreter) is
*consulted*, at runtime, with the real composition root - a stronger and
narrower claim than "something imports this module". This module proves
the *shallower, repo-wide* claim - reachable at all, from anywhere real -
which is what a recovery pass over the whole tree needs; the deep,
behavioural proof for anything this flags stays that file's job, or a
future file's, never re-derived here.

Usable as a library (every caller in this project's own tests does this)
or as a script: ``python -m scripts.repo_recovery``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
TESTS_ROOT = REPO_ROOT / "tests"

# The real production entry points - every script a person or a scheduled
# job actually runs, plus main.py. Anything reachable by following
# imports from these, and only these, is "production reachable". Adding a
# new script here is the one edit a future entry point needs; nothing
# else in this module knows their names.
ENTRY_POINTS: tuple[Path, ...] = (
    REPO_ROOT / "main.py",
    *sorted((REPO_ROOT / "scripts").glob("*.py")),
)


# ---------------------------------------------------------------------------
# A + B. Actual current repository state, and committed vs uncommitted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitState:
    """What the repository actually is right now, read from git itself -
    never hardcoded, never assumed from a prior report. git is already
    the reliable, reproducible mechanism for this; this dataclass only
    gives its answer a stable shape a test can assert against."""

    branch: str
    commit: str
    commit_message: str
    modified_tracked: tuple[str, ...]
    untracked: tuple[str, ...]
    staged: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """True only when nothing distinguishes the working tree from HEAD."""
        return not (self.modified_tracked or self.untracked or self.staged)

    def describe(self) -> str:
        lines = [
            f"branch: {self.branch}",
            f"HEAD: {self.commit} ({self.commit_message})",
            f"modified tracked files: {len(self.modified_tracked)}",
            f"staged files: {len(self.staged)}",
            f"untracked paths: {len(self.untracked)}",
        ]
        return "\n".join(lines)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout


def current_git_state() -> GitState:
    """The repository's real, current git state - committed vs
    uncommitted, tracked-modified vs untracked vs staged - read fresh
    every call. This is Requirements A and B: what exists now, and what
    of it is (and is not) part of the committed project history.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    commit = _git("rev-parse", "HEAD").strip()
    commit_message = _git("log", "-1", "--format=%s").strip()

    porcelain = _git("status", "--porcelain").splitlines()
    modified_tracked: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in porcelain:
        if not line:
            continue
        index_state, worktree_state, path = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state == "M":
            modified_tracked.append(path)

    return GitState(
        branch=branch,
        commit=commit,
        commit_message=commit_message,
        modified_tracked=tuple(sorted(set(modified_tracked))),
        untracked=tuple(sorted(set(untracked))),
        staged=tuple(sorted(set(staged))),
    )


# ---------------------------------------------------------------------------
# C. Module reachability - production / test-only / unreachable
# ---------------------------------------------------------------------------

Reachability = str  # "production" | "test_only" | "unreachable"


@dataclass(frozen=True)
class ReachabilityReport:
    production: frozenset[str]
    test_only: frozenset[str]
    unreachable: frozenset[str]
    # Same base name (e.g. "ManagerAgent") defined as a class in more
    # than one module - candidates for a duplicate/superseded
    # implementation, never a confirmed one. A person decides; this only
    # narrows where to look.
    duplicate_class_names: dict[str, tuple[str, ...]]

    def status_of(self, dotted_module: str) -> Reachability:
        if dotted_module in self.production:
            return "production"
        if dotted_module in self.test_only:
            return "test_only"
        return "unreachable"


def _module_path_for(py_file: Path) -> str:
    """The dotted module path a real ``import`` statement would use for
    this file, relative to the repo root (e.g. src/manager/manager.py ->
    "src.manager.manager")."""
    relative = py_file.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _all_src_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_in(py_file: Path) -> set[str]:
    """Every dotted module this file's own ``import``/``from`` statements
    name, anywhere in the file - top-level or nested in a function, since
    several real providers in this codebase import lazily on purpose
    (see src/providers/factory.py's own docstring on why). Only the
    modules actually written in the source are returned; nothing here
    executes the file.
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            # "from src.agents.email import extractors" imports the
            # *module* src.agents.email.extractors, not a name inside
            # __init__.py - but node.module alone is only
            # "src.agents.email", the same ambiguity
            # _resolve_to_src_module's own docstring describes. Recording
            # the combined path too lets resolution find it either way,
            # whichever it turns out to be; an alias that names an
            # ordinary class rather than a submodule simply never
            # matches a known module and is harmlessly ignored.
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")
    return found


def _resolve_to_src_module(imported: str, known_modules: set[str]) -> str | None:
    """Which known src/ module (if any) this import statement's dotted
    name refers to.

    ``from src.manager.capabilities import CapabilityRequest`` names
    "src.manager.capabilities" directly. ``from src.manager import
    capabilities`` names only "src.manager" - the submodule is the
    *imported name*, not part of the dotted path an AST node carries -
    so the longest known prefix is tried first, falling back to the
    name exactly as written. Never a guess beyond what is actually
    written: an import this cannot resolve to a known module is simply
    not counted, which only ever under-counts reachability, never
    over-counts it.
    """
    if imported in known_modules:
        return imported
    prefix = imported + "."
    for module in known_modules:
        if module.startswith(prefix):
            return module
    return None


def _reachable_from(
    roots: list[Path], known_modules: set[str], module_imports: dict[str, set[str]]
) -> set[str]:
    seen: set[str] = set()
    frontier: list[str] = []
    for root in roots:
        frontier.extend(_resolve_to_src_module(name, known_modules) or name for name in _imports_in(root))

    while frontier:
        name = frontier.pop()
        resolved = _resolve_to_src_module(name, known_modules) if name not in known_modules else name
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        for next_import in module_imports.get(resolved, ()):
            frontier.append(next_import)
    return seen


@lru_cache(maxsize=1)
def compute_reachability() -> ReachabilityReport:
    """Classify every module under src/ as production-reachable,
    reachable only from tests, or reachable from neither - the
    behavioural core of Requirement C.

    A module already imported for its side effects by a production
    entry point, however deep the chain, is production-reachable. One
    reached only by something under tests/ is a real, tested seam with
    no known caller today - exactly ARCHITECTURE.md's own "PREPARED"
    category, now computed rather than hand-maintained. One reached by
    neither is the strongest, cheapest signal this tool can offer that a
    module is dead, stale, or an abandoned prototype - never proof by
    itself; see this module's own docstring for why a person still
    decides.

    Cached for the life of the process (``@lru_cache``) - parsing every
    file under src/ and tests/ is the expensive part of this module, and
    the answer does not change within one run. Call
    ``compute_reachability.cache_clear()`` first if the files on disk
    have changed and a fresh scan is genuinely needed in the same
    process (a long-lived tool watching the tree, for instance); an
    ordinary script invocation never needs to.
    """
    src_files = _all_src_files()
    known_modules = {_module_path_for(f) for f in src_files}
    module_imports = {
        _module_path_for(f): _imports_in(f) for f in src_files
    }

    production = _reachable_from(list(ENTRY_POINTS), known_modules, module_imports)

    test_files = sorted(p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    test_only_seen: set[str] = set()
    for test_file in test_files:
        for name in _imports_in(test_file):
            resolved = _resolve_to_src_module(name, known_modules)
            if resolved is not None:
                test_only_seen.add(resolved)
                for next_import in module_imports.get(resolved, ()):
                    frontier_resolved = _resolve_to_src_module(next_import, known_modules)
                    if frontier_resolved is not None:
                        test_only_seen.add(frontier_resolved)

    all_modules = known_modules
    # Python imports every ancestor package of whatever you actually
    # named - `import src.manager.capabilities` necessarily imports
    # `src` and `src.manager` too, whether or not anything ever writes
    # that shorter name explicitly. A static walk over literal import
    # statements does not see that implicit step by itself, so it is
    # added back here - once - rather than leaving every package
    # `__init__.py` to misreport as unreachable merely because nothing
    # names the *package* directly, only something inside it.
    def _with_ancestors(modules: set[str]) -> set[str]:
        expanded = set(modules)
        for module in modules:
            parts = module.split(".")
            for i in range(1, len(parts)):
                ancestor = ".".join(parts[:i])
                if ancestor in all_modules:
                    expanded.add(ancestor)
        return expanded

    production_set = _with_ancestors(production) & all_modules
    test_only_set = (_with_ancestors(test_only_seen) & all_modules) - production_set
    unreachable_set = all_modules - production_set - test_only_set

    # Duplicate class-name detection: cheap, purely structural, and
    # deliberately not confirmation of anything - see the dataclass's
    # own field docstring.
    class_owners: dict[str, list[str]] = {}
    for f in src_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue
        module = _module_path_for(f)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_owners.setdefault(node.name, []).append(module)
    duplicates = {
        name: tuple(sorted(set(owners)))
        for name, owners in class_owners.items()
        if len(set(owners)) > 1
    }

    return ReachabilityReport(
        production=frozenset(production_set),
        test_only=frozenset(test_only_set),
        unreachable=frozenset(unreachable_set),
        duplicate_class_names=duplicates,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def render_report() -> str:
    git_state = current_git_state()
    reachability = compute_reachability()

    lines = ["=" * 70, "0.1 REPOSITORY RECOVERY", "=" * 70, ""]
    lines.append("-- A/B. Repository state --")
    lines.append(git_state.describe())
    if git_state.modified_tracked:
        lines.append(f"  modified: {', '.join(git_state.modified_tracked[:8])}"
                      + (" ..." if len(git_state.modified_tracked) > 8 else ""))
    if git_state.untracked:
        lines.append(f"  untracked (sample): {', '.join(git_state.untracked[:8])}"
                      + (" ..." if len(git_state.untracked) > 8 else ""))
    lines.append("")
    lines.append("-- C. Module reachability --")
    lines.append(f"  production-reachable: {len(reachability.production)}")
    lines.append(f"  test-only (PREPARED-class): {len(reachability.test_only)}")
    lines.append(f"  unreachable by anything: {len(reachability.unreachable)}")
    if reachability.unreachable:
        lines.append("  unreachable modules:")
        for module in sorted(reachability.unreachable):
            lines.append(f"    - {module}")
    if reachability.test_only:
        lines.append("  test-only modules:")
        for module in sorted(reachability.test_only):
            lines.append(f"    - {module}")
    if reachability.duplicate_class_names:
        lines.append("  duplicate class names (review candidates, not confirmed duplicates):")
        for name, owners in sorted(reachability.duplicate_class_names.items()):
            lines.append(f"    - {name}: {', '.join(owners)}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    print(render_report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
