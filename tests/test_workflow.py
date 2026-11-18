from pathlib import Path
import re
import unittest


class WorkflowTests(unittest.TestCase):
    def test_private_pipeline_has_no_public_source_or_report_sinks(self):
        path = Path(__file__).parents[1] / '.github/workflows/private-ci.yml'
        self.assertTrue(path.is_file(), 'Private-source workflow is missing')
        text = path.read_text()
        for required in ['repository_dispatch:', 'release:', 'SOURCE_READ_SSH_KEY', 'persist-credentials: false', 'package-manager-cache: false', 'private_checkout.py', 'private_build.py node', 'private_build.py kit', 'private_build.py ios']:
            self.assertIn(required, text)
        for forbidden in ['upload-artifact', 'actions/cache', 'pull_request_target', 'gh release upload', 'set -x']:
            self.assertNotIn(forbidden, text)
        self.assertNotIn('github.event.client_payload', text, 'Unvalidated parameters must not be printed in public run names or shell code')
        uses = re.findall(r'uses:\s*([^\s]+)', text)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', action) for action in uses))
        self.assertEqual(text.count('if: always()'), 3)


if __name__ == '__main__':
    unittest.main()
