from pathlib import Path
import importlib
import unittest


class RequestTests(unittest.TestCase):
    def test_dispatch_accepts_only_a_pinned_sha_and_profile(self):
        self.assertTrue((Path(__file__).parents[1] / 'scripts/ci_request.py').is_file(), 'Request validator is missing')
        validate = importlib.import_module('scripts.ci_request').validate_request
        sha = 'a' * 40
        self.assertEqual(validate('repository_dispatch', {'action': 'openstella-ci', 'client_payload': {'sha': sha, 'target': 'all'}}, 'owner', 'owner'), {'sha': sha, 'target': 'all'})

    def test_rejects_untrusted_or_malformed_dispatch_without_echoing_it(self):
        validate = importlib.import_module('scripts.ci_request').validate_request
        valid = {'sha': 'a' * 40, 'target': 'all'}
        bad = [dict(valid, command='echo PRIVATE_SENTINEL'), dict(valid, repo='attacker/repo'), dict(valid, url='https://attacker.invalid/repo'), dict(valid, ref='refs/heads/feature'), dict(valid, branch='feature'), dict(valid, approved_sha='b' * 40), dict(valid, ci_sha='b' * 40), dict(valid, sha='a' * 39), dict(valid, sha='$(echo PRIVATE_SENTINEL)'), dict(valid, target='all\nPRIVATE_SENTINEL'), {'sha': 'a' * 40}, [], None]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as error:
                    validate('repository_dispatch', {'action': 'openstella-ci', 'client_payload': payload}, 'owner', 'owner')
                self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
        with self.assertRaises(ValueError):
            validate('repository_dispatch', {'action': 'openstella-ci', 'client_payload': valid}, 'outsider', 'owner')
        with self.assertRaises(ValueError):
            validate('pull_request_target', {'client_payload': valid}, 'owner', 'owner')

    def test_release_and_manual_triggers_use_the_same_strict_parameters(self):
        validate = importlib.import_module('scripts.ci_request').validate_request
        sha = 'b' * 40
        self.assertEqual(validate('release', {'action': 'published', 'release': {'tag_name': 'ci-' + sha, 'draft': False}}, 'owner', 'owner'), {'sha': sha, 'target': 'all'})
        self.assertEqual(validate('workflow_dispatch', {'inputs': {'sha': sha, 'target': 'kit'}}, 'owner', 'owner'), {'sha': sha, 'target': 'kit'})
        for release in [{'tag_name': 'ci-' + sha, 'draft': True}, {'tag_name': 'v1.0.0', 'draft': False}]:
            with self.assertRaises(ValueError):
                validate('release', {'action': 'published', 'release': release}, 'owner', 'owner')


    def test_cli_writes_validated_outputs_and_rejects_fork_context(self):
        import json, os, subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / 'event.json'
            output = Path(tmp) / 'output'
            event.write_text(json.dumps({'action': 'openstella-ci', 'client_payload': {'sha': 'c' * 40, 'target': 'node'}}))
            env = dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_OUTPUT=str(output), GITHUB_EVENT_NAME='repository_dispatch', GITHUB_ACTOR='robinliang0829-lgtm', GITHUB_REPOSITORY='robinliang0829-lgtm/OpenStella-CI')
            script = Path(__file__).parents[1] / 'scripts/ci_request.py'
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
            result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output.exists(), 'CLI did not emit validated outputs')
            self.assertEqual(output.read_text(), 'sha=' + 'c' * 40 + '\ntarget=node\nci_sha=' + ci_sha + '\n')
            output.unlink()
            for invalid in ('0' * 40, '', 'main', 'A' * 40):
                env['GITHUB_WORKFLOW_SHA'] = invalid
                rejected = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse(output.exists(), 'Rejected workflow must not emit outputs')
                self.assertNotIn(invalid or 'PRIVATE_CANARY', rejected.stdout + rejected.stderr)
            env['GITHUB_WORKFLOW_SHA'] = ci_sha
            env['GITHUB_WORKSPACE'] = str(Path(tmp) / 'missing-workspace')
            rejected = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(output.exists())
            self.assertNotIn('missing-workspace', rejected.stdout + rejected.stderr)
            env['GITHUB_WORKSPACE'] = str(workspace)
            env.pop('GITHUB_WORKFLOW_SHA')
            rejected = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(output.exists())
            env['GITHUB_WORKFLOW_SHA'] = ci_sha
            env['GITHUB_REPOSITORY'] = 'outsider/fork'
            result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
