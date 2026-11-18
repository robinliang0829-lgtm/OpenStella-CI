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


if __name__ == '__main__':
    unittest.main()
