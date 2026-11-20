from pathlib import Path
import contextlib
import importlib
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def make_origin(root, trusted_refs=('main',)):
    origin = root / 'origin'
    origin.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(origin), *args], stderr=subprocess.DEVNULL, text=True).strip()

    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (origin / 'data').write_text('PRIVATE_SOURCE_CANARY')
    git('add', 'data')
    git('commit', '-qm', 'trusted fixture')
    main = git('rev-parse', 'HEAD')
    commits = {'main': main}
    for ref in trusted_refs:
        if ref == 'main':
            continue
        git('checkout', '-qb', ref, 'main')
        (origin / 'data').write_text(f'{ref} canary')
        git('commit', '-qam', f'{ref} fixture')
        commits[ref] = git('rev-parse', 'HEAD')
    git('checkout', '-qb', 'untrusted', 'main')
    (origin / 'data').write_text('UNTRUSTED_SOURCE_CANARY')
    git('commit', '-qam', 'untrusted fixture')
    commits['untrusted'] = git('rev-parse', 'HEAD')
    (origin / 'data').write_text('UNTRUSTED_SOURCE_CANARY_2')
    git('commit', '-qam', 'untrusted fixture 2')
    commits['untrusted2'] = git('rev-parse', 'HEAD')
    git('checkout', '-q', 'main')
    return origin, git, commits


class WorldCoreCheckoutTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'scripts/world_core_checkout.py'
        self.assertTrue(path.is_file(), 'World Core checkout helper is missing')
        self.helper = importlib.import_module('scripts.world_core_checkout')

    def test_trusted_refs_are_reviewed_and_approvals_default_to_empty(self):
        self.assertEqual(getattr(self.helper, 'APPROVED_FEATURE_SHAS', None), frozenset())
        self.assertEqual(tuple(getattr(self.helper, 'TRUSTED_REFS', ())), ('refs/heads/main',))
        self.assertTrue(self.helper.SOURCE_URL.endswith('openastra-world-core.git'))
        self.assertNotIn('OpenStella.git', self.helper.SOURCE_URL)

    def test_checks_out_only_commits_inside_a_trusted_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, _git, commits = make_origin(root, trusted_refs=('main',))
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(self.helper, 'SOURCE_URL', str(origin)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.helper.checkout(commits['main'], 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'main-ok')
                self.assertEqual((root / 'main-ok/data').read_text(), 'PRIVATE_SOURCE_CANARY')
                with self.assertRaises(ValueError):
                    self.helper.checkout(commits['untrusted'], 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'denied')
                with self.assertRaises(ValueError):
                    self.helper.checkout('$(injection)', 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'invalid')
            self.assertEqual(list(root.glob('.source-key-*')), [])
            self.assertNotIn('PRIVATE_SOURCE_CANARY', stdout.getvalue() + stderr.getvalue())
            self.assertNotIn('FIXTURE_KEY', stdout.getvalue() + stderr.getvalue())

    def test_approval_is_exact_and_does_not_grant_other_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, _git, commits = make_origin(root, trusted_refs=('main',))
            with patch.object(self.helper, 'SOURCE_URL', str(origin)), patch.object(self.helper, 'APPROVED_FEATURE_SHAS', frozenset({commits['untrusted']})):
                self.helper.checkout(commits['untrusted'], 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'approved')
                with self.assertRaises(ValueError):
                    self.helper.checkout(commits['untrusted2'], 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'not-approved')

    def test_missing_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.helper.checkout('a' * 40, '', Path(tmp) / 'dest')


if __name__ == '__main__':
    unittest.main()
