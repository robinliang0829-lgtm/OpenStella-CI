from pathlib import Path
import contextlib
import importlib
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class CheckoutTests(unittest.TestCase):
    def test_only_main_history_is_checked_out_and_ssh_key_is_removed(self):
        self.assertTrue((Path(__file__).parents[1] / 'scripts/private_checkout.py').is_file(), 'Private checkout helper is missing')
        helper = importlib.import_module('scripts.private_checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / 'origin'
            def git(*args):
                return subprocess.check_output(['git', '-C', str(origin), *args], stderr=subprocess.DEVNULL, text=True).strip()
            origin.mkdir()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (origin / 'data').write_text('PRIVATE_SOURCE_CANARY')
            git('add', 'data')
            git('commit', '-qm', 'trusted fixture')
            trusted = git('rev-parse', 'HEAD')
            git('checkout', '-qb', 'untrusted')
            (origin / 'data').write_text('UNTRUSTED_SOURCE_CANARY')
            git('commit', '-qam', 'untrusted fixture')
            untrusted = git('rev-parse', 'HEAD')
            git('checkout', '-q', 'main')
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(helper, 'SOURCE_URL', str(origin)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                helper.checkout(trusted, 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'approved')
                self.assertEqual((root / 'approved/data').read_text(), 'PRIVATE_SOURCE_CANARY')
                with self.assertRaises(ValueError):
                    helper.checkout(untrusted, 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'denied')
                with self.assertRaises(ValueError):
                    helper.checkout('$(injection)', 'FIXTURE_KEY_NOT_A_REAL_CREDENTIAL', root / 'invalid')
            self.assertEqual(list(root.glob('.source-key-*')), [])
            self.assertNotIn('PRIVATE_SOURCE_CANARY', stdout.getvalue() + stderr.getvalue())
            self.assertNotIn('FIXTURE_KEY', stdout.getvalue() + stderr.getvalue())


    def make_history(self, root):
        origin = root / 'origin'
        origin.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(origin), *args], stderr=subprocess.DEVNULL, text=True).strip()
        git('init', '-q', '-b', 'main')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        (origin / 'data').write_text('main')
        git('add', '.')
        git('commit', '-qm', 'main')
        main = git('rev-parse', 'HEAD')
        git('checkout', '-qb', 'feature')
        commits = []
        for value in ('parent', 'approved', 'child'):
            (origin / 'data').write_text(value)
            git('commit', '-qam', value)
            commits.append(git('rev-parse', 'HEAD'))
        git('checkout', '-qb', 'other', main)
        (origin / 'data').write_text('other')
        git('commit', '-qam', 'other')
        other = git('rev-parse', 'HEAD')
        git('checkout', '-q', 'main')
        return origin, git, main, commits, other

    def test_static_approval_is_exact_and_not_inherited(self):
        import os
        helper = importlib.import_module('scripts.private_checkout')
        self.assertEqual(getattr(helper, 'APPROVED_FEATURE_SHAS', None), frozenset({'3b5c00ebe78eb72394c35de23c7ecc26f80f8e2f'}), 'Only the explicitly reviewed snapshot may be approved')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, git, main, commits, other = self.make_history(root)
            parent, approved, child = commits
            with patch.object(helper, 'SOURCE_URL', str(origin)), patch.object(helper, 'APPROVED_FEATURE_SHAS', frozenset({approved})), patch.dict(os.environ, {'APPROVED_FEATURE_SHAS': ','.join((parent, child, other)), 'APPROVED_SHA': other}):
                helper.checkout(approved, 'FIXTURE_KEY', root / 'approved')
                self.assertEqual((root / 'approved/data').read_text(), 'approved')
                self.assertEqual(subprocess.check_output(['git', '-C', str(root / 'approved'), 'rev-parse', 'HEAD'], text=True).strip(), approved)
                for index, denied in enumerate((parent, child, other, '0' * 40)):
                    with self.subTest(sha=denied), self.assertRaises(ValueError):
                        helper.checkout(denied, 'FIXTURE_KEY', root / f'denied-{index}')
                helper.checkout(main, 'FIXTURE_KEY', root / 'main')
            self.assertEqual(list(root.glob('.source-key-*')), [])

    def test_approved_object_must_be_a_commit(self):
        helper = importlib.import_module('scripts.private_checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, git, main, commits, other = self.make_history(root)
            git('tag', '-am', 'annotated fixture', 'fixture-tag', main)
            objects = [git('rev-parse', 'fixture-tag'), git('rev-parse', main + '^{tree}'), git('rev-parse', main + ':data')]
            with patch.object(helper, 'SOURCE_URL', str(origin)), patch.object(helper, 'APPROVED_FEATURE_SHAS', frozenset(objects)):
                for index, sha in enumerate(objects):
                    with self.subTest(kind=git('cat-file', '-t', sha)), self.assertRaises(ValueError):
                        helper.checkout(sha, 'FIXTURE_KEY', root / f'denied-{index}')
            self.assertEqual(list(root.glob('.source-key-*')), [])

    def test_head_mismatch_and_git_failures_reject_without_leaking_key(self):
        import os
        helper = importlib.import_module('scripts.private_checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, git, main, commits, other = self.make_history(root)
            real_run = subprocess.run
            for scenario in ('head-mismatch', 'head-error', 'fetch-error'):
                seen_head = []
                def intercept(args, **kwargs):
                    self.assertNotIn('SOURCE_READ_SSH_KEY', kwargs['env'])
                    if 'rev-parse' in args and args[-1] == 'HEAD':
                        seen_head.append(True)
                        return subprocess.CompletedProcess(args, 1 if scenario == 'head-error' else 0, stdout=(other + '\n').encode(), stderr=b'PRIVATE_CANARY')
                    if scenario == 'fetch-error' and 'fetch' in args:
                        return subprocess.CompletedProcess(args, 1, stdout=b'PRIVATE_CANARY', stderr=b'PRIVATE_CANARY')
                    return real_run(args, **kwargs)
                output = io.StringIO()
                with self.subTest(scenario=scenario), patch.object(helper, 'SOURCE_URL', str(origin)), patch.dict(os.environ, {'SOURCE_READ_SSH_KEY': 'FIXTURE_KEY'}), patch.object(helper.subprocess, 'run', side_effect=intercept), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    with self.assertRaises(ValueError):
                        helper.checkout(main, 'FIXTURE_KEY', root / scenario)
                self.assertEqual(list(root.glob('.source-key-*')), [])
                self.assertNotIn('PRIVATE_CANARY', output.getvalue())
                self.assertNotIn('FIXTURE_KEY', output.getvalue())
                if scenario != 'fetch-error':
                    self.assertTrue(seen_head)

    def test_cli_secret_trailing_newline_is_restored_before_ssh(self):
        import os, shlex
        helper = importlib.import_module('scripts.private_checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture_key = root / 'fixture-key'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(fixture_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # gh secret set intentionally trims final CR/LF bytes from stdin.
            trimmed = fixture_key.read_text().rstrip('\r\n')
            origin = root / 'origin'
            origin.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(origin), *args], stderr=subprocess.DEVNULL, text=True).strip()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (origin / 'fixture').write_text('fixture')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            sha = git('rev-parse', 'HEAD')
            original_run = subprocess.run
            def verify_key_then_run(args, **kwargs):
                env = kwargs['env']
                self.assertNotIn('SOURCE_READ_SSH_KEY', env)
                ssh = shlex.split(env['GIT_SSH_COMMAND'])
                key_path = ssh[ssh.index('-i') + 1]
                parsed = original_run(['ssh-keygen', '-y', '-f', key_path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.assertEqual(parsed.returncode, 0, 'Serialized Actions key is not parseable by OpenSSH')
                return original_run(args, **kwargs)
            with patch.object(helper, 'SOURCE_URL', str(origin)), patch.dict(os.environ, {'SOURCE_READ_SSH_KEY': trimmed}), patch.object(helper.subprocess, 'run', side_effect=verify_key_then_run):
                helper.checkout(sha, trimmed, root / 'approved')
            self.assertEqual(list(root.glob('.source-key-*')), [])


if __name__ == '__main__':
    unittest.main()
