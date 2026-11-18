"""Safety contract for the credential-free runner capability probe."""
from pathlib import Path
import unittest


class BootstrapTests(unittest.TestCase):
    def test_probe_has_no_private_checkout_or_secrets(self):
        workflow = Path(__file__).parents[1] / '.github/workflows/runner-smoke.yml'
        self.assertTrue(workflow.is_file(), 'Credential-free probe workflow is missing')
        text = workflow.read_text()
        self.assertIn('repository_dispatch:', text)
        self.assertIn('release:', text)
        self.assertIn('permissions: {}', text)
        self.assertIn('ubuntu-latest', text)
        self.assertIn('macos-15', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('actions/checkout', text)
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('git clone', text)


if __name__ == '__main__':
    unittest.main()
