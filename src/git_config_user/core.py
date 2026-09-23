"""Repo discovery, identity reading/writing and settings. No GTK here, so it can be tested."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Directories that never contain repos worth listing and are expensive to walk.
SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", ".cache", ".tox", ".mypy_cache",
    ".pytest_cache", ".gradle", ".m2", ".cargo", ".rustup", ".npm", ".pnpm-store",
    "target", "dist", "build", ".terraform", ".idea", ".vscode", "vendor",
})

# The app never opens prompts and must keep working on repos owned by other users.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
GIT_BASE = ["git", "-c", "safe.directory=*"]

STATUS_OK = "ok"
STATUS_WRONG = "wrong"        # a profile rule matches but the identity is another one
STATUS_UNKNOWN = "unknown"    # identity not in any profile
STATUS_MISSING = "missing"    # user.name or user.email not set at all
STATUS_ERROR = "error"


# -- settings ---------------------------------------------------------------

@dataclass
class Profile:
    name: str
    email: str
    # fnmatch patterns tested against the remote URL and the repo path.
    patterns: list[str] = field(default_factory=list)

    def matches(self, repo: RepoInfo) -> bool:
        targets = [str(repo.path)] + ([repo.remote, normalize_remote(repo.remote)] if repo.remote else [])
        return any(fnmatch.fnmatch(t, p) for p in self.patterns for t in targets)

    @property
    def label(self) -> str:
        return f"{self.name} <{self.email}>"


def normalize_remote(url: str) -> str:
    """`git@github.com:a/b.git` and `https://u@github.com/a/b` both become `github.com/a/b...`."""
    url = url.strip()
    if "://" in url:
        url = url.split("://", 1)[1]
    elif re.match(r"^[^/]+:", url):  # scp-like syntax
        url = url.replace(":", "/", 1)
    host, _, path = url.partition("/")
    host = host.rsplit("@", 1)[-1]
    return f"{host}/{path}"


@dataclass
class Settings:
    base_dir: str = ""
    interval: int = 10  # seconds between automatic rescans
    profiles: list[Profile] = field(default_factory=list)


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "infranettone-git-config-user" / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return Settings()
    profiles = [Profile(p.get("name", ""), p.get("email", ""), list(p.get("patterns", [])))
                for p in data.get("profiles", [])]
    return Settings(base_dir=data.get("base_dir", ""), interval=int(data.get("interval", 10)),
                    profiles=profiles)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


# -- discovery ----------------------------------------------------------------

def find_repos(base: Path) -> list[Path]:
    """Every directory under `base` with a `.git` entry (dir, or file for worktrees/submodules).

    Keeps descending into repos, so nested repos and submodules are listed too.
    Symlinks are not followed to avoid cycles.
    """
    repos: list[Path] = []
    for root, dirs, files in os.walk(base, onerror=lambda _e: None):
        if ".git" in dirs or ".git" in files:
            repos.append(Path(root))
        dirs[:] = sorted(d for d in dirs if d != ".git" and d not in SKIP_DIRS
                         and not os.path.islink(os.path.join(root, d)))
    return repos


# -- reading ------------------------------------------------------------------

@dataclass
class Value:
    value: str = ""
    scope: str = ""   # system | global | local | worktree | command
    origin: str = ""  # file the value comes from


@dataclass
class RepoInfo:
    path: Path
    name: Value = field(default_factory=Value)
    email: Value = field(default_factory=Value)
    remote: str = ""
    error: str = ""
    # Files whose mtime decides whether this repo has to be read again.
    sources: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name.value, self.email.value)

    @property
    def is_local(self) -> bool:
        return self.name.scope in ("local", "worktree") or self.email.scope in ("local", "worktree")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*GIT_BASE, "-C", str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check, timeout=15)


def parse_config_z(out: str) -> list[tuple[str, str, str, str]]:
    """Parses `git config -z --show-scope --show-origin --get-regexp` into (scope, origin, key, value)."""
    parts = out.split("\0")
    entries = []
    for i in range(0, len(parts) - 2, 3):
        scope, origin, kv = parts[i], parts[i + 1], parts[i + 2]
        key, _, value = kv.partition("\n")
        entries.append((scope, origin, key, value))
    return entries


def _origin_file(repo: Path, origin: str) -> str:
    if not origin.startswith("file:"):
        return ""
    p = Path(origin[5:])
    return str(p if p.is_absolute() else repo / p)


def read_repo(repo: Path) -> RepoInfo:
    info = RepoInfo(path=repo)
    proc = _git(repo, "config", "-z", "--show-scope", "--show-origin", "--get-regexp",
                r"^(user\.(name|email)|remote\.origin\.url)$", check=False)
    # Exit 1 just means no key matched.
    if proc.returncode not in (0, 1):
        info.error = proc.stderr.strip() or f"git config terminó con código {proc.returncode}"
        return info
    sources = set()
    for scope, origin, key, value in parse_config_z(proc.stdout):
        f = _origin_file(repo, origin)
        if f:
            sources.add(f)
        v = Value(value, scope, f)
        # Later entries override earlier ones, same as git.
        if key == "user.name":
            info.name = v
        elif key == "user.email":
            info.email = v
        elif key == "remote.origin.url":
            info.remote = value
    sources.add(str(git_config_file(repo)))
    sources.update(global_config_files())
    info.sources = tuple(sorted(sources))
    return info


def git_config_file(repo: Path) -> Path:
    dotgit = repo / ".git"
    if dotgit.is_file():  # worktree or submodule: "gitdir: <path>"
        try:
            target = dotgit.read_text().strip().removeprefix("gitdir:").strip()
            gitdir = Path(target) if Path(target).is_absolute() else (repo / target).resolve()
            common = gitdir / "commondir"
            if common.is_file():
                gitdir = (gitdir / common.read_text().strip()).resolve()
            return gitdir / "config"
        except OSError:
            pass
    return dotgit / "config"


def global_config_files() -> list[str]:
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    files = [os.environ.get("GIT_CONFIG_GLOBAL") or str(home / ".gitconfig"),
             str(xdg / "git" / "config"), "/etc/gitconfig"]
    return files


def signature(sources: tuple[str, ...]) -> tuple:
    sig = []
    for f in sources:
        try:
            st = os.stat(f)
            sig.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((f, None, None))
    return tuple(sig)


class Scanner:
    """Keeps the last result per repo and only re-reads repos whose config files changed."""

    def __init__(self):
        self._cache: dict[Path, tuple[tuple, RepoInfo]] = {}

    def scan(self, base: Path) -> list[RepoInfo]:
        result, cache = [], {}
        for repo in find_repos(base):
            cached = self._cache.get(repo)
            if cached and cached[0] == signature(cached[1].sources):
                info = cached[1]
            else:
                try:
                    info = read_repo(repo)
                except (OSError, subprocess.SubprocessError) as e:
                    info = RepoInfo(path=repo, error=str(e))
            cache[repo] = (signature(info.sources), info)
            result.append(info)
        self._cache = cache
        return result

    def invalidate(self, repo: Path | None = None) -> None:
        if repo is None:
            self._cache.clear()
        else:
            self._cache.pop(repo, None)


# -- evaluation ---------------------------------------------------------------

def expected_profile(repo: RepoInfo, profiles: list[Profile]) -> Profile | None:
    return next((p for p in profiles if p.patterns and p.matches(repo)), None)


def evaluate(repo: RepoInfo, profiles: list[Profile]) -> tuple[str, Profile | None]:
    """Returns (status, expected profile or None)."""
    if repo.error:
        return STATUS_ERROR, None
    expected = expected_profile(repo, profiles)
    if not repo.name.value or not repo.email.value:
        return STATUS_MISSING, expected
    if expected is not None:
        ok = same_identity(repo.identity, (expected.name, expected.email))
        return (STATUS_OK if ok else STATUS_WRONG), expected
    if any(same_identity(repo.identity, (p.name, p.email)) for p in profiles):
        return STATUS_OK, None
    return STATUS_UNKNOWN, None


def same_identity(a: tuple[str, str], b: tuple[str, str]) -> bool:
    return a[0] == b[0] and a[1].strip().lower() == b[1].strip().lower()


# -- writing ------------------------------------------------------------------

def set_local_identity(repo: Path, name: str, email: str) -> None:
    _git(repo, "config", "--local", "user.name", name)
    _git(repo, "config", "--local", "user.email", email)


def unset_local_identity(repo: Path) -> None:
    for key in ("user.name", "user.email"):
        # Exit 5 means the key was not set: nothing to remove.
        proc = _git(repo, "config", "--local", "--unset-all", key, check=False)
        if proc.returncode not in (0, 5):
            raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
