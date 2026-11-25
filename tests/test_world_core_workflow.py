from pathlib import Path
import importlib
import re
import unittest


class WorldCoreWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]
        self.workflow = self.root / '.github/workflows/world-core-ci.yml'
        self.assertTrue(self.workflow.is_file(), 'World Core workflow is missing')
        self.text = self.workflow.read_text()

    def test_jobs_use_the_validated_ci_snapshot(self):
        jobs = dict(re.findall(r'^  (request|node):\n(.*?)(?=^  [a-z]+:|\Z)', self.text, re.MULTILINE | re.DOTALL))
        self.assertEqual(set(jobs), {'request', 'node'})
        self.assertIn('ci_sha: ${{ steps.request.outputs.ci_sha }}', jobs['request'])
        self.assertRegex(jobs['request'], r'with:\n\s+ref: main\n\s+persist-credentials: false')
        self.assertIn('needs: request', jobs['node'])
        self.assertRegex(jobs['node'], r'with:\n\s+ref: \$\{\{ needs.request.outputs.ci_sha \}\}\n\s+persist-credentials: false')
        self.assertIn('permissions:\n  contents: read\n', self.text)
        self.assertEqual(self.text.count('ref: '), 2)

    def test_no_public_source_or_report_sinks(self):
        for required in [
            'repository_dispatch:',
            'openastra-world-core-ci',
            'release:',
            'wc-ci-',
            'WORLD_CORE_SOURCE_READ_SSH_KEY',
            'persist-credentials: false',
            'package-manager-cache: false',
            'world_core_checkout.py',
            'world_core_build.py "$WORLD_CORE_PROFILE"',
            "options: [all, node, ope78, ope110]",
            "needs.request.outputs.target == 'ope78'",
            "needs.request.outputs.target == 'ope110'",
            'OPE110_EVIDENCE_DIR: ${{ runner.temp }}/world-core-output/authority-evidence',
            "'26.13.0' || '24'",
        ]:
            self.assertIn(required, self.text)
        for forbidden in ['upload-artifact', 'actions/cache', 'pull_request_target', 'gh release upload', 'set -x', 'github.event.client_payload']:
            self.assertNotIn(forbidden, self.text)
        uses = re.findall(r'uses:\s*([^\s]+)', self.text)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', action) for action in uses))
        self.assertEqual(self.text.count('if: always()'), 1)


class WorldCoreBuildTests(unittest.TestCase):
    def test_pilot_runs_fixed_contract_resident_and_rust_stages(self):
        module = importlib.import_module('scripts.world_core_build')
        stages = dict(module.build_plan('ope78'))
        self.assertEqual(stages['pilot-authority-contract'], ['node', 'contract/verify-contract.mjs', '--authority'])
        self.assertEqual(stages['pilot-resident'], ['node', '--test', 'resident/pilot.test.ts'])
        self.assertIn('--locked', stages['pilot-rust-tests'])
        self.assertIn('--locked', stages['pilot-rust-release'])
        self.assertEqual(stages['pilot-shadow-parity'], ['node', 'qa/check-authority.mjs', '--shadow'])

    def test_authority_runs_node_gates_locked_rust_and_live_http(self):
        module = importlib.import_module('scripts.world_core_build')
        node = module.build_plan('node')
        authority = module.build_plan('ope110')
        self.assertEqual(authority[:len(node)], node)
        project = ['--manifest-path', 'rust/Cargo.toml', '--locked']
        self.assertEqual(authority[len(node):], [
            ('authority-rust-toolchain', ['rustup', 'toolchain', 'install', '1.98.0', '--profile', 'minimal']),
            ('authority-rust-tests', ['cargo', '+1.98.0', 'test', *project, '--workspace']),
            ('authority-rust-build', ['cargo', '+1.98.0', 'build', *project]),
            ('authority-http', ['node', '--test', 'tests/authority/world-authority.test.mjs']),
        ])

    def test_build_plan_is_fixed_and_never_reads_request_data(self):
        module = importlib.import_module('scripts.world_core_build')
        node = module.build_plan('node')
        self.assertEqual(module.build_plan('all'), node)
        labels = [label for label, _args in node]
        for required in ('npm-install', 'typecheck', 'unit-suites', 'contract-gate', 'migration-guard', 'bundle-freshness', 'e2e-drill', 'cross-runtime-parity', 'asset-hashes', 'release-gate'):
            self.assertIn(required, labels)
        for label, args in node:
            self.assertRegex(label, r'^[a-z0-9-]+$')
            self.assertIsInstance(args, list)
            self.assertTrue(all(isinstance(part, str) for part in args))
            self.assertNotIn('sh', args)
            self.assertNotIn('bash', args)
        with self.assertRaises(ValueError):
            module.build_plan('ios')
        with self.assertRaises(ValueError):
            module.build_plan('all; curl attacker')


if __name__ == '__main__':
    unittest.main()
