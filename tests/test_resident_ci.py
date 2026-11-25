from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import resident_ci


class ResidentCiTests(unittest.TestCase):
    sha = '9873b42da759db1d648f6e24858f60b24c88a1ec'

    def test_only_approved_sha_and_owner_are_accepted(self):
        owner, repo = resident_ci.CI_OWNER, resident_ci.CI_REPOSITORY
        event = {'inputs': {'sha': self.sha}}
        self.assertEqual(resident_ci.validate_request('workflow_dispatch', event, owner, repo), self.sha)
        for trigger, inputs, actor, repository in [
            ('pull_request', event, owner, repo),
            ('workflow_dispatch', event, 'other', repo),
            ('workflow_dispatch', event, owner, 'other/ci'),
            ('workflow_dispatch', {'inputs': {'sha': 'a' * 40}}, owner, repo),
            ('workflow_dispatch', {'inputs': {'sha': self.sha, 'url': 'other'}}, owner, repo),
        ]:
            with self.subTest(trigger=trigger, inputs=inputs, actor=actor, repository=repository):
                with self.assertRaises(ValueError):
                    resident_ci.validate_request(trigger, inputs, actor, repository)

    @patch.object(resident_ci, 'checkout')
    def test_checkout_uses_only_the_resident_remote_and_scoped_key(self, checkout):
        with self.assertRaises(ValueError):
            resident_ci.checkout_resident('a' * 40, 'placeholder', Path('destination'))
        checkout.assert_not_called()
        resident_ci.checkout_resident(self.sha, 'placeholder', Path('destination'))
        checkout.assert_called_once_with(
            self.sha, 'placeholder', Path('destination'),
            source_url='git@github.com:robinliang0829-lgtm/OpenAstra-Resident.git',
            approved_feature_shas=frozenset({self.sha}), key_env='RESIDENT_SOURCE_READ_SSH_KEY',
        )

    @patch.object(resident_ci, 'run_stage')
    @patch.dict('os.environ', {'RESIDENT_SOURCE_READ_SSH_KEY': 'placeholder'})
    def test_build_runs_fixed_stages_without_the_read_key(self, run_stage):
        resident_ci.build(Path('source'), Path('logs'))
        stages = {call.args[0]: call.args[1] for call in run_stage.call_args_list}
        self.assertEqual(stages['cli'], ['npm', '--prefix', 'gingoo-cli', 'test'])
        self.assertEqual(stages['resident-and-miniapp'], ['npm', 'run', 'test:ci'])
        self.assertTrue(all(resident_ci.KEY_ENV not in call.kwargs['env']
                            for call in run_stage.call_args_list))
