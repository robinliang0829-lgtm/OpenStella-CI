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
