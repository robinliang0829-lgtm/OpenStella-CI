from pathlib import Path
import importlib
import unittest


class WorldCoreRequestTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).parents[1] / 'scripts/world_core_request.py'
        self.assertTrue(self.path.is_file(), 'World Core request validator is missing')
        self.validate = importlib.import_module('scripts.world_core_request').validate_request
        self.sha = 'a' * 40

    def dispatch(self, payload):
        return {'action': 'openastra-world-core-ci', 'client_payload': payload}

    def test_accepts_only_a_pinned_sha_and_profile(self):
        self.assertEqual(
            self.validate('repository_dispatch', self.dispatch({'sha': self.sha, 'target': 'all'}), 'owner', 'owner'),
            {'sha': self.sha, 'target': 'all'},
        )
        self.assertEqual(
            self.validate('repository_dispatch', self.dispatch({'sha': self.sha, 'target': 'node'}), 'owner', 'owner'),
            {'sha': self.sha, 'target': 'node'},
        )

    def test_rejects_untrusted_or_malformed_dispatch_without_echoing_it(self):
        valid = {'sha': self.sha, 'target': 'all'}
        bad = [
            dict(valid, command='echo PRIVATE_SENTINEL'),
            dict(valid, repo='attacker/repo'),
            dict(valid, url='https://attacker.invalid/repo'),
            dict(valid, ref='refs/heads/feature'),
            dict(valid, ci_sha='b' * 40),
            dict(valid, sha='a' * 39),
            dict(valid, sha='$(echo PRIVATE_SENTINEL)'),
            dict(valid, target='all\nPRIVATE_SENTINEL'),
            dict(valid, target='ios'),
            {'sha': self.sha},
            [],
            None,
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as error:
                    self.validate('repository_dispatch', self.dispatch(payload), 'owner', 'owner')
                self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
        with self.assertRaises(ValueError):
            self.validate('repository_dispatch', self.dispatch(valid), 'outsider', 'owner')
        with self.assertRaises(ValueError):
            self.validate('pull_request_target', {'client_payload': valid}, 'owner', 'owner')
        # The OpenStella event type must not drive the world-core pipeline.
        with self.assertRaises(ValueError):
            self.validate('repository_dispatch', {'action': 'openstella-ci', 'client_payload': valid}, 'owner', 'owner')

    def test_release_and_manual_triggers_use_the_same_strict_parameters(self):
        sha = 'b' * 40
        self.assertEqual(
            self.validate('release', {'action': 'published', 'release': {'tag_name': 'wc-ci-' + sha, 'draft': False}}, 'owner', 'owner'),
            {'sha': sha, 'target': 'all'},
        )
        self.assertEqual(
            self.validate('workflow_dispatch', {'inputs': {'sha': sha, 'target': 'node'}}, 'owner', 'owner'),
            {'sha': sha, 'target': 'node'},
        )
        for release in [
            {'tag_name': 'wc-ci-' + sha, 'draft': True},
            {'tag_name': 'ci-' + sha, 'draft': False},
            {'tag_name': 'wc-ci-short', 'draft': False},
        ]:
            with self.assertRaises(ValueError):
                self.validate('release', {'action': 'published', 'release': release}, 'owner', 'owner')

    def test_cli_writes_validated_outputs_and_rejects_foreign_context(self):
        import json, os, subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / 'event.json'
            output = Path(tmp) / 'output'
            event.write_text(json.dumps(self.dispatch({'sha': 'c' * 40, 'target': 'node'})))
            env = dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_OUTPUT=str(output), GITHUB_EVENT_NAME='repository_dispatch', GITHUB_ACTOR='robinliang0829-lgtm', GITHUB_REPOSITORY='robinliang0829-lgtm/OpenStella-CI')
            workspace = Path(tmp) / 'ci'
            workspace.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(workspace), *args], stderr=subprocess.DEVNULL, text=True).strip()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('commit', '--allow-empty', '-qm', 'trusted CI fixture')
            ci_sha = git('rev-parse', 'HEAD')
            env.update(GITHUB_WORKSPACE=str(workspace), GITHUB_WORKFLOW_SHA=ci_sha)
            result = subprocess.run([sys.executable, str(self.path)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), 'sha=' + 'c' * 40 + '\ntarget=node\nci_sha=' + ci_sha + '\n')
            output.unlink()
            for invalid in ('0' * 40, '', 'main', 'A' * 40):
                env['GITHUB_WORKFLOW_SHA'] = invalid
                rejected = subprocess.run([sys.executable, str(self.path)], env=env, capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse(output.exists())
                self.assertNotIn(invalid or 'PRIVATE_CANARY', rejected.stdout + rejected.stderr)
            env['GITHUB_WORKFLOW_SHA'] = ci_sha
            env['GITHUB_REPOSITORY'] = 'outsider/fork'
            self.assertNotEqual(subprocess.run([sys.executable, str(self.path)], env=env, capture_output=True, text=True).returncode, 0)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
