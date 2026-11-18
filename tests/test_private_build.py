from pathlib import Path
import contextlib
import importlib
import io
import sys
import tempfile
import unittest


class BuildTests(unittest.TestCase):
    def test_failing_tool_output_is_private_and_not_echoed(self):
        self.assertTrue((Path(__file__).parents[1] / 'scripts/private_build.py').is_file(), 'Private build runner is missing')
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                with self.assertRaises(helper.StageFailed):
                    helper.run_stage('fixture', [sys.executable, '-c', 'import sys; print("PRIVATE_SOURCE_CANARY"); print("PRIVATE_STDERR_CANARY", file=sys.stderr); sys.exit(7)'], root, root / 'logs')
            self.assertNotIn('PRIVATE_SOURCE_CANARY', public.getvalue())
            self.assertNotIn('PRIVATE_STDERR_CANARY', public.getvalue())
            self.assertIn('PRIVATE_SOURCE_CANARY', (root / 'logs/fixture.log').read_text())
            self.assertIn('fixture: failed', public.getvalue())


    def test_profiles_preserve_original_ci_gates(self):
        helper = importlib.import_module('scripts.private_build')
        self.assertTrue(hasattr(helper, 'build_plan'), 'Build plans are missing')
        commands = [args for _, args in helper.build_plan('node')]
        for required in [['npm', 'ci'], ['npx', 'biome', 'ci', '.'], ['node', '--test', 'ios/UITestServer/server.test.mjs'], ['npm', 'run', 'typecheck'], ['npm', 'run', 'lint', '-w', 'contracts'], ['npm', 'test'], ['npm', 'run', 'build']]:
            self.assertIn(required, commands)
        self.assertIn(['swift', 'test', '--package-path', 'ios/Packages/OpenStellaKit'], [args for _, args in helper.build_plan('kit')])
        with self.assertRaises(ValueError):
            helper.build_plan('arbitrary-command')

    def test_simulator_selection_respects_the_installed_sdk(self):
        helper = importlib.import_module('scripts.private_build')
        self.assertTrue(hasattr(helper, 'choose_simulator'), 'Simulator selector is missing')
        devices = {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [{'name': 'iPhone fixture', 'udid': 'compatible'}], 'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [{'name': 'iPhone future', 'udid': 'too-new'}]}
        self.assertEqual(helper.choose_simulator('26.2', devices), 'compatible')
        with self.assertRaises(ValueError):
            helper.choose_simulator('16.4', devices)


    def test_ios_orchestration_uses_a_private_result_bundle_and_cleans_server(self):
        from unittest.mock import patch, MagicMock
        helper = importlib.import_module('scripts.private_build')
        self.assertTrue(hasattr(helper, 'run_profile'), 'Build entrypoint is missing')
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            process = MagicMock()
            process.poll.return_value = None
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [{'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}
            with patch.object(helper, 'run_stage') as run, patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'):
                helper.run_profile('ios', source, root / 'logs')
            project = next(call for call in run.call_args_list if call.args[0] == 'xcode-project')
            self.assertEqual(project.args[2], source / 'ios')
            self.assertEqual(project.args[1], ['xcodegen', 'generate'])
            args = run.call_args_list[-1].args[1]
            self.assertEqual(args[:2], ['xcodebuild', 'test'])
            self.assertIn('CODE_SIGNING_ALLOWED=NO', args)
            self.assertIn('id=fixture-udid', args)
            self.assertIn(str(root / 'logs/app.xcresult'), args)
            process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
