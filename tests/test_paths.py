import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uni_learn.paths import locate_project, project_id, roots, safe_id


class PathTests(unittest.TestCase):
    def test_same_basename_distinct_roots_and_resolved_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a" / "same"
            b = Path(tmp) / "b" / "same"
            a.mkdir(parents=True)
            b.mkdir(parents=True)
            self.assertNotEqual(project_id(a), project_id(b))
            self.assertEqual(project_id(a), project_id(a / ".." / "same"))

    def test_xdg_and_windows_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = roots({"HOME": tmp, "XDG_STATE_HOME": str(Path(tmp) / "state space")})
            self.assertEqual(result["state"], Path(tmp) / "state space" / "uni-learn")
            win = roots({"HOME": tmp, "APPDATA": str(Path(tmp) / "Roaming"), "LOCALAPPDATA": str(Path(tmp) / "Local")}, platform="nt")
            self.assertEqual(win["config"], Path(tmp) / "Roaming" / "uni-learn")
            self.assertEqual(win["data"], Path(tmp) / "Local" / "data" / "uni-learn")
            self.assertNotEqual(win["state"], win["cache"])

    def test_reject_path_identifiers(self):
        for value in ("../x", "a/b", "", "é"):
            with self.assertRaises(ValueError):
                safe_id(value)

    def test_empty_or_relative_environment_roots_use_user_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            for platform in ("posix", "nt"):
                expected = roots({"HOME": tmp}, platform=platform)
                for invalid in ("", "relative/path", ".", "~/state"):
                    env = {"HOME": tmp, "APPDATA": invalid, "LOCALAPPDATA": invalid}
                    env.update({"XDG_" + key.upper() + "_HOME": invalid for key in expected})
                    with self.subTest(platform=platform, value=invalid):
                        self.assertEqual(roots(env, platform=platform), expected)
                        self.assertTrue(all(path.is_absolute() for path in roots(env, platform=platform).values()))

    def test_invalid_home_never_creates_cwd_relative_roots(self):
        for invalid in ("", "relative/home"):
            with self.subTest(home=invalid):
                self.assertTrue(all(path.is_absolute() for path in roots({"HOME": invalid}).values()))

    def test_invalid_process_home_uses_native_user_home(self):
        for invalid in ("", "relative/home"):
            with self.subTest(home=invalid), patch.dict(os.environ, {"HOME": invalid}, clear=True):
                if os.name == "posix":
                    import pwd
                    expected = Path(pwd.getpwuid(os.getuid()).pw_dir)
                    self.assertEqual(roots()["state"], expected / ".local/state/uni-learn")
                else:
                    with self.assertRaises(ValueError):
                        roots()

    def test_locate_nested_repository_and_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repository = base / "repo α"
            nested = repository / "src/module"
            nested.mkdir(parents=True)
            (repository / ".git").mkdir()
            self.assertEqual(locate_project(nested)["project_root"], str(repository))
            worktree = base / "worktree"
            (worktree / "src").mkdir(parents=True)
            (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/worktree\n")
            self.assertEqual(locate_project(worktree / "src")["project_root"], str(worktree))
            self.assertNotEqual(locate_project(nested)["project_id"], locate_project(worktree)["project_id"])

    def test_locate_plain_directory_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory).resolve() / "plain"
            plain.mkdir()
            result = locate_project(plain)
            self.assertEqual(result, {"project_root": str(plain), "project_id": project_id(plain), "source": "directory"})
            self.assertEqual(list(plain.iterdir()), [])
            with self.assertRaises(FileNotFoundError):
                locate_project(plain / "missing")
            file = plain / "file.txt"
            file.write_text("fixture")
            with self.assertRaises(ValueError):
                locate_project(file)
