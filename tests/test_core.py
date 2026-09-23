import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from git_config_user import core  # noqa: E402


class IsolatedHome(unittest.TestCase):
    """Real git against a temporary HOME: the user's ~/.gitconfig is never read or written."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.base = self.tmp / "repos"
        self.base.mkdir()
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def init(self, rel, remote=""):
        path = self.base / rel
        path.mkdir(parents=True)
        self.git("init", "-q", str(path))
        if remote:
            self.git("remote", "add", "origin", remote, cwd=path)
        return path


class FindReposTest(IsolatedHome):
    def test_nested_and_skipped(self):
        a = self.init("a")
        b = self.init("group/b")
        nested = self.init("a/sub")
        self.init("a/node_modules/dep")
        (self.base / "plain").mkdir()
        self.assertEqual(core.find_repos(self.base), [a, nested, b])

    def test_worktree_config_file(self):
        a = self.init("a")
        self.git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "i", cwd=a)
        wt = self.base / "wt"
        self.git("worktree", "add", "-q", str(wt), cwd=a)
        self.assertIn(wt, core.find_repos(self.base))
        self.assertEqual(core.git_config_file(wt).resolve(), (a / ".git" / "config").resolve())


class ReadRepoTest(IsolatedHome):
    def test_global_then_local(self):
        a = self.init("a", "git@github.com:acme/a.git")
        info = core.read_repo(a)
        self.assertEqual(info.identity, ("", ""))
        self.assertEqual(info.remote, "git@github.com:acme/a.git")

        self.git("config", "--global", "user.name", "Global Name")
        self.git("config", "--global", "user.email", "global@example.com")
        info = core.read_repo(a)
        self.assertEqual(info.identity, ("Global Name", "global@example.com"))
        self.assertEqual(info.email.scope, "global")
        self.assertFalse(info.is_local)

        core.set_local_identity(a, "Work Name", "work@acme.com")
        info = core.read_repo(a)
        self.assertEqual(info.identity, ("Work Name", "work@acme.com"))
        self.assertEqual(info.email.scope, "local")
        self.assertTrue(info.is_local)

        core.unset_local_identity(a)
        core.unset_local_identity(a)  # idempotent
        self.assertEqual(core.read_repo(a).email.scope, "global")

    def test_include_if(self):
        a = self.init("work/a")
        inc = self.home / "work.gitconfig"
        inc.write_text("[user]\n\tname = Inc\n\temail = inc@work.com\n")
        self.git("config", "--global", f"includeIf.gitdir:{self.base}/work/.path", str(inc))
        info = core.read_repo(a)
        self.assertEqual(info.identity, ("Inc", "inc@work.com"))
        self.assertIn(str(inc), info.sources)

    def test_values_with_spaces_and_equals(self):
        a = self.init("a")
        core.set_local_identity(a, "Ana María = López", "a@b.c")
        self.assertEqual(core.read_repo(a).name.value, "Ana María = López")


class ScannerTest(IsolatedHome):
    def test_rereads_only_changed_repos(self):
        a = self.init("a")
        self.init("b")
        scanner = core.Scanner()
        scanner.scan(self.base)
        with mock.patch.object(core, "read_repo", wraps=core.read_repo) as spy:
            scanner.scan(self.base)
            self.assertEqual(spy.call_count, 0)
            core.set_local_identity(a, "N", "n@x")
            os.utime(core.git_config_file(a), ns=(1, 1))  # mtime resolution safety
            result = {r.path: r for r in scanner.scan(self.base)}
            self.assertEqual(spy.call_count, 1)
        self.assertEqual(result[a].identity, ("N", "n@x"))

    def test_global_change_invalidates_all(self):
        self.init("a")
        self.init("b")
        scanner = core.Scanner()
        scanner.scan(self.base)
        self.git("config", "--global", "user.email", "g@x")
        result = scanner.scan(self.base)
        self.assertEqual([r.email.value for r in result], ["g@x", "g@x"])


def repo(path="/r/a", name="N", email="n@x", remote=""):
    return core.RepoInfo(path=Path(path), name=core.Value(name, "local"), email=core.Value(email, "local"),
                         remote=remote)


class EvaluateTest(unittest.TestCase):
    work = core.Profile("Work", "me@acme.com", ["*github.com/acme/*", "/work/*"])
    personal = core.Profile("Me", "me@home.org")

    def status(self, r):
        return core.evaluate(r, [self.work, self.personal])[0]

    def test_statuses(self):
        self.assertEqual(self.status(repo(name="", email="")), core.STATUS_MISSING)
        self.assertEqual(self.status(repo(name="Me", email="ME@home.org")), core.STATUS_OK)
        self.assertEqual(self.status(repo(name="X", email="x@y")), core.STATUS_UNKNOWN)
        self.assertEqual(self.status(repo(name="Me", email="me@home.org", remote="git@github.com:acme/api.git")),
                         core.STATUS_WRONG)
        self.assertEqual(self.status(repo(path="/work/x", name="Work", email="me@acme.com")), core.STATUS_OK)

    def test_expected_reported_even_when_missing(self):
        status, exp = core.evaluate(repo(path="/work/x", name=""), [self.work])
        self.assertEqual((status, exp), (core.STATUS_MISSING, self.work))


class SettingsTest(unittest.TestCase):
    def test_roundtrip_and_missing_file(self):
        path = Path(tempfile.mkdtemp()) / "sub" / "settings.json"
        self.assertEqual(core.load_settings(path), core.Settings())
        s = core.Settings(base_dir="/x", interval=5, profiles=[core.Profile("A", "a@b", ["*"])])
        core.save_settings(s, path)
        self.assertEqual(core.load_settings(path), s)


class ParseTest(unittest.TestCase):
    def test_normalize_remote(self):
        for url in ("git@github.com:acme/a.git", "https://github.com/acme/a.git",
                    "ssh://git@github.com/acme/a.git", "https://user:tok@github.com/acme/a.git"):
            self.assertEqual(core.normalize_remote(url), "github.com/acme/a.git", url)

    def test_parse_config_z(self):
        out = "global\0file:/h/.gitconfig\0user.name\nA\0local\0file:.git/config\0user.name\nB\nC\0"
        self.assertEqual(core.parse_config_z(out), [("global", "file:/h/.gitconfig", "user.name", "A"),
                                                    ("local", "file:.git/config", "user.name", "B\nC")])


if __name__ == "__main__":
    unittest.main()
