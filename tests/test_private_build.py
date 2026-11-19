from pathlib import Path
import contextlib
import importlib
import io
import sys
import tempfile
import unittest


@contextlib.contextmanager
def failing_log_reads(error):
    """Inject read failures without breaking private fixture writes."""
    from unittest.mock import patch
    original_open = Path.open

    def open_file(path, mode='r', *args, **kwargs):
        if mode in ('r', 'rb'):
            raise error
        return original_open(path, mode, *args, **kwargs)

    with patch.object(Path, 'open', open_file):
        yield


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


    def test_app_failure_classifier_returns_only_unknown_for_unrecognized_private_text(self):
        helper = importlib.import_module('scripts.private_build')
        self.assertTrue(hasattr(helper, 'classify_app_tests_failure'), 'Safe classifier is missing')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            log.write_text('PRIVATE_SOURCE_CANARY sk-fake-key /private/source/Hidden.swift')
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                result = helper.classify_app_tests_failure(log)
            self.assertEqual(result, 'unknown')
            self.assertEqual(public.getvalue(), '')

    def test_app_failure_classifier_recognizes_member_or_api_diagnostics_without_sdk_claim(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for diagnostic in [
                "error: value of type 'Fixture' has no member 'typo'",
                "error: type 'Fixture' has no member 'typo'",
                "error: 'fixture' is only available in iOS 99.0 or newer",
                "error: 'fixture' is unavailable in iOS",
            ]:
                with self.subTest(diagnostic=diagnostic):
                    log.write_text('PRIVATE_SOURCE_CANARY sk-fake-key\n' + diagnostic)
                    self.assertEqual(helper.classify_app_tests_failure(log), 'compile-member-api')

    def test_app_failure_classifier_prioritizes_xctest_assertions_over_build_summaries(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for assertion in [
                'error: -[Fixture testPrivate] : XCTAssertEqual failed: has no member fixture',
                'error: XCTAssertTrue failed - PRIVATE_SOURCE_CANARY sk-fake-key',
                "error: -[Fixture testPrivate] : failed - is only available in iOS fixture",
                'error: XCTAssertEqual failed: linker command failed',
                'error: XCTAssertEqual failed: Failed to launch the app',
                'error: XCTAssertEqual failed: Unable to find a destination matching the provided destination specifier:',
            ]:
                with self.subTest(assertion=assertion):
                    log.write_text(assertion + '\n** TEST BUILD FAILED **\n')
                    self.assertEqual(helper.classify_app_tests_failure(log), 'test-assertion')

    def test_xctunwrap_assertion_body_is_not_compiler_link_or_launch_evidence(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for message in [
                'error: cannot find fixture in scope',
                'error: value of type Fixture has no member missing',
                'linker command failed',
                'Failed to launch the app',
                'Failed to boot simulator',
            ]:
                with self.subTest(message=message):
                    log.write_text('error: -[FixtureTests testExample] : XCTUnwrap failed: '
                                   'expected non-nil value of type String - ' + message)
                    public = io.StringIO()
                    with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                        self.assertEqual(helper.classify_app_tests_failure(log), 'test-assertion')
                    self.assertEqual(public.getvalue(), '')

    def test_timeout_words_in_assertions_or_quoted_text_are_not_timeout_evidence(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for timeout in ['Test execution timed out',
                            'Exceeded execution time allowance of 60 seconds',
                            'Asynchronous wait failed: Exceeded timeout of 60 seconds']:
                for assertion in ['XCTAssertEqual failed: actual text differs from expected ',
                                  'XCTUnwrap failed: expected non-nil value of type String - ',
                                  'failed - quoted diagnostic: ']:
                    with self.subTest(timeout=timeout, assertion=assertion):
                        log.write_text('error: -[FixtureTests testExample] : ' + assertion + timeout)
                        self.assertEqual(helper.classify_app_tests_failure(log), 'test-assertion')
                for quotation in ['"' + timeout + '"', "expected text: '" + timeout + "'",
                                  '> ' + timeout]:
                    with self.subTest(quotation=quotation):
                        log.write_text(quotation)
                        self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')

    def assert_build_reason(self, expected, diagnostic):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            log.write_text('/private/Hidden.swift:12:3: error: ' + diagnostic
                           + '\nPRIVATE_SOURCE_CANARY sk-fake-key testPrivate\n** TEST BUILD FAILED **\n')
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                self.assertEqual(helper.classify_app_tests_failure(log), 'test-build')
                self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), expected)
            self.assertEqual(public.getvalue(), '')

    def test_build_reason_unknown_keeps_test_build_fallback(self):
        self.assert_build_reason('test-build', 'unrecognized PRIVATE_SOURCE_CANARY')

    def test_build_reason_actor_isolation(self):
        self.assert_build_reason('build-actor-isolation', 'call to main actor-isolated instance method fixture() in a synchronous nonisolated context')
        self.assert_build_reason('build-actor-isolation', 'main actor-isolated property fixture cannot be referenced from a nonisolated context')

    def test_build_reason_signature_mismatch(self):
        self.assert_build_reason('build-argument-shape', "missing argument for parameter 'fixture' in call")
        self.assert_build_reason('build-argument-shape', 'extra argument fixture in call')
        self.assert_build_reason('build-argument-shape', 'incorrect argument label in call (have: fixture:, expected: other:)')
        self.assert_build_reason('build-argument-shape', 'no exact matches in call to initializer')

    def test_build_reason_try_await(self):
        self.assert_build_reason('build-try-await', "call can throw but is not marked with 'try'")
        self.assert_build_reason('build-try-await', "expression is 'async' but is not marked with 'await'")

    def test_build_reason_override_conformance(self):
        self.assert_build_reason('build-override-conformance', 'type Fixture does not conform to protocol FixtureProtocol')
        self.assert_build_reason('build-override-conformance', 'method does not override any method from its superclass')

    def test_build_reason_generic_inference(self):
        self.assert_build_reason('build-type-inference', 'generic parameter Fixture could not be inferred')
        self.assert_build_reason('build-type-inference', 'the compiler is unable to type-check this expression in reasonable time; try breaking up the expression into distinct sub-expressions')
        self.assert_build_reason('build-type-inference', 'ambiguous use of fixture')

    def test_build_reason_syntax_parser(self):
        self.assert_build_reason('build-syntax-parser', 'expected expression')
        self.assert_build_reason('build-syntax-parser', 'expected declaration')

    def test_build_reason_module_import(self):
        self.assert_build_reason('build-module-import', "no such module 'Fixture'")
        self.assert_build_reason('build-module-import', 'missing required module Fixture')

    def test_build_reason_compiler_internal(self):
        self.assert_build_reason('build-compiler-internal', 'failed to produce diagnostic for expression; please submit a bug report')

    def test_build_reason_resource_limit(self):
        self.assert_build_reason('build-resource-limit', 'out of memory')
        self.assert_build_reason('build-resource-limit', 'unable to make temporary file: No space left on device')

    def test_build_reason_status_is_supplemental_best_effort_and_build_only(self):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        examples = [
            ('actor-isolation', 'main actor-isolated property fixture cannot be referenced'),
            ('argument-shape', 'incorrect argument labels in call (have: fixture:, expected: other:)'),
            ('try-await', "call can throw but is not marked with 'try'"),
            ('override-conformance', 'type Fixture does not conform to protocol FixtureProtocol'),
            ('type-inference', 'the compiler is unable to type-check this expression in reasonable time'),
            ('syntax-parser', 'expected expression'),
            ('module-import', 'no such module Fixture'),
            ('compiler-internal', 'failed to produce diagnostic for expression'),
            ('resource-limit', 'out of memory'),
            (None, 'unrecognized PRIVATE_SOURCE_CANARY'),
        ]
        for reason, diagnostic in examples:
            for fault in [None, 'read', 'classifier', 'write', 'flush']:
                with self.subTest(reason=reason, fault=fault), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate')
                    process = MagicMock()
                    process.poll.return_value = None
                    process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
                    devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                        {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                    def stage(label, args, cwd, logs):
                        if label == 'app-tests':
                            (logs / 'app-tests.log').write_text('error: ' + diagnostic
                                + '\nPRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate\n** BUILD FAILED **\n')
                            print('app-tests: failed', flush=True)
                            raise original

                    real_classifier = helper.classify_app_tests_failure
                    def classify(log, **kwargs):
                        if kwargs.get('build_reason'):
                            if fault == 'read':
                                with failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY')):
                                    return real_classifier(log, **kwargs)
                            if fault == 'classifier':
                                raise helper.StageFailed('PRIVATE_DIAGNOSTIC_FAILURE')
                        return real_classifier(log, **kwargs)

                    class Output(io.StringIO):
                        def write(self, text):
                            if fault == 'write' and text.startswith('app-tests-build-'):
                                raise OSError('PRIVATE_SOURCE_CANARY')
                            return super().write(text)
                        def flush(self):
                            if fault == 'flush' and 'app-tests-build-' in self.getvalue():
                                raise MemoryError('PRIVATE_SOURCE_CANARY')
                            return super().flush()

                    public, stderr = Output(), io.StringIO()
                    with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'), patch.object(helper, 'classify_app_tests_failure', side_effect=classify) as classified:
                        with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                            with self.assertRaises(helper.StageFailed) as caught:
                                helper.run_profile('ios', root, root / 'logs')
                    self.assertIs(caught.exception, original)
                    expected = 'app-tests: failed\napp-tests-test-build: failed\n'
                    if reason and fault in (None, 'flush'):
                        expected += f'app-tests-build-{reason}: failed\n'
                    self.assertEqual(public.getvalue(), expected)
                    self.assertEqual(stderr.getvalue(), '')
                    self.assertEqual(classified.call_count, 2)
                    self.assertEqual(classified.call_args.kwargs, {'build_reason': True})
                    process.terminate.assert_called_once_with()
                    process.kill.assert_called_once_with()
                    self.assertEqual(process.wait.call_count, 2)
                    self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})

    def test_build_reason_explanatory_location_is_not_diagnostic(self):
        self.assert_referenced_build_diagnostic_safe(
            'expected text: /private/Hidden.swift:12:3: error: expected expression')

    def test_build_reason_blockquoted_location_is_not_diagnostic(self):
        self.assert_referenced_build_diagnostic_safe(
            '> /private/Hidden.swift:12:3: error: no such module Fixture')

    def test_build_reason_quoted_location_is_not_diagnostic(self):
        self.assert_referenced_build_diagnostic_safe(
            '"/private/Hidden.swift:12:3: error: out of memory"')

    def test_build_reason_warning_location_is_not_diagnostic(self):
        self.assert_referenced_build_diagnostic_safe(
            'warning: /private/Hidden.swift:12:3: error: expected expression')

    def test_build_reason_note_location_is_not_diagnostic(self):
        self.assert_referenced_build_diagnostic_safe(
            'note: /private/Hidden.swift:12:3: error: no such module Fixture')

    def assert_referenced_build_diagnostic_safe(self, diagnostic):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / 'app-tests.log'
            for summary, expected in [('', 'unknown'), ('\n** BUILD FAILED **\n', 'test-build')]:
                with self.subTest(layer='classifier', summary=bool(summary)):
                    log.write_text(diagnostic + summary)
                    public, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                        self.assertEqual(helper.classify_app_tests_failure(log), expected)
                        self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), expected)
                    self.assertEqual(public.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), '')

            original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key testPrivate')
            process = MagicMock()
            process.poll.return_value = None
            process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

            def stage(label, args, cwd, logs):
                if label == 'app-tests':
                    (logs / 'app-tests.log').write_text(diagnostic
                        + '\nPRIVATE_SOURCE_CANARY sk-fake-key testPrivate\n** BUILD FAILED **\n')
                    print('app-tests: failed', flush=True)
                    raise original

            public, stderr = io.StringIO(), io.StringIO()
            with self.subTest(layer='orchestration'):
                with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'):
                    with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                        with self.assertRaises(helper.StageFailed) as caught:
                            helper.run_profile('ios', root, root / 'logs')
                self.assertIs(caught.exception, original)
                self.assertEqual(public.getvalue(), 'app-tests: failed\napp-tests-test-build: failed\n')
                self.assertEqual(stderr.getvalue(), '')
                process.terminate.assert_called_once_with()
                process.kill.assert_called_once_with()
                self.assertEqual(process.wait.call_count, 2)
                self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})
                self.assertEqual(process.wait.call_args_list[1].args, ())
                self.assertEqual(process.wait.call_args_list[1].kwargs, {})

    def test_build_reason_quotes_non_build_evidence_and_read_failures_stay_safe(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                for diagnostic, expected in [
                    ('error: missing argument for parameter fixture in call', 'unknown'),
                    ('error: expected expression\n** BUILD FAILED **', 'build-syntax-parser'),
                    ('"error: expected expression"\n** BUILD FAILED **', 'test-build'),
                    ('expected text: error: ambiguous use of fixture\n** BUILD FAILED **', 'test-build'),
                    ('> error: no exact matches in call to fixture\n** BUILD FAILED **', 'test-build'),
                    ('/private/Hidden.swift:not-line:3: error: out of memory\n** BUILD FAILED **', 'test-build'),
                    ('warning: no such module Fixture\n** BUILD FAILED **', 'test-build'),
                    ('error: "no such module Fixture"\n** BUILD FAILED **', 'test-build'),
                    ('error: -[Fixture testPrivate] : XCTUnwrap failed: error: expected expression\n** BUILD FAILED **', 'test-assertion'),
                    ('error: XCTAssertEqual failed: quoted\nerror: ambiguous use of fixture\n** BUILD FAILED **', 'test-assertion'),
                    ('error: cannot find fixture in scope\nerror: expected expression\n** BUILD FAILED **', 'compile'),
                    ('Test execution timed out\nerror: no such module Fixture\n** BUILD FAILED **', 'test-timeout'),
                ]:
                    with self.subTest(expected=expected):
                        log.write_text(diagnostic)
                        self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), expected)
                log.write_bytes(b'\xfferror: out of memory\n** BUILD FAILED **')
                self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), 'unknown')
                log.write_bytes(b'error: out of memory\n** BUILD FAILED **\n' + b' ' * (1024 * 1024))
                self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), 'unknown')
                with failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY')):
                    self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), 'unknown')
                log.unlink()
                self.assertEqual(helper.classify_app_tests_failure(log, build_reason=True), 'unknown')
            self.assertEqual(public.getvalue(), '')

    def test_app_failure_classifier_recognizes_explicit_test_build_failures(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for diagnostic in ['** TEST BUILD FAILED **', '** BUILD FAILED **', 'Testing cancelled because the build failed.']:
                with self.subTest(diagnostic=diagnostic):
                    log.write_text('PRIVATE_SOURCE_CANARY sk-fake-key\n' + diagnostic)
                    self.assertEqual(helper.classify_app_tests_failure(log), 'test-build')
            log.write_text('error: PRIVATE_SOURCE_CANARY sk-fake-key\n** TEST FAILED **')
            self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')

    def test_app_failure_classifier_requires_explicit_simulator_failure_evidence(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for diagnostic in [
                'CoreSimulatorService connection became invalid.',
                'Failed to boot simulator',
                'Unable to boot the Simulator',
                'Failed to establish communication with the test runner',
            ]:
                with self.subTest(diagnostic=diagnostic):
                    log.write_text('PRIVATE_SOURCE_CANARY sk-fake-key\n' + diagnostic + '\n** TEST FAILED **')
                    self.assertEqual(helper.classify_app_tests_failure(log), 'simulator-startup-communication')
            log.write_text('CoreSimulator booted\nSimulator connected\nerror: unrelated PRIVATE_SOURCE_CANARY')
            self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')

    def test_app_failure_classifier_distinguishes_other_explicit_failure_categories(self):
        helper = importlib.import_module('scripts.private_build')
        fixtures = [
            ('test-timeout', 'Test execution timed out'),
            ('test-timeout', 'Exceeded execution time allowance of 60 seconds'),
            ('test-timeout', 'Asynchronous wait failed: Exceeded timeout of 60 seconds'),
            ('install-launch', 'Failed to install the app on the device'),
            ('install-launch', 'Failed to launch the app'),
            ('destination', 'Unable to find a destination matching the provided destination specifier:'),
            ('destination', 'Ineligible destinations for the fixture scheme:'),
            ('link', 'Undefined symbols for architecture arm64:'),
            ('link', 'clang: error: linker command failed with exit code 1'),
            ('compile', "error: cannot find 'fixture' in scope"),
            ('compile', 'error: cannot convert value of type fixture'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for expected, diagnostic in fixtures:
                with self.subTest(category=expected, diagnostic=diagnostic):
                    log.write_text('PRIVATE_SOURCE_CANARY sk-fake-key\n' + diagnostic + '\n** TEST FAILED **')
                    self.assertEqual(helper.classify_app_tests_failure(log), expected)

    def test_app_failure_classifier_read_failures_are_silent_unknown(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                try:
                    self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
                    log.write_bytes(b'\xffPRIVATE_SOURCE_CANARY sk-fake-key')
                    self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
                    with failing_log_reads(OSError('PRIVATE_SOURCE_CANARY sk-fake-key')):
                        self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
                except (OSError, UnicodeError):
                    self.fail('Classifier must silently return unknown on unreadable logs')
            self.assertEqual(public.getvalue(), '')

    def test_ios_app_failure_emits_only_fixed_category_preserves_failure_and_cleans_server(self):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        fixtures = [
            ('compile-member-api', "error: value of type 'Fixture' has no member 'typo'"),
            ('compile', "error: cannot find 'fixture' in scope"),
            ('link', 'Undefined symbols for architecture arm64:'),
            ('destination', 'Unable to find a destination matching the provided destination specifier:'),
            ('simulator-startup-communication', 'Failed to boot simulator'),
            ('install-launch', 'Failed to launch the app'),
            ('test-assertion', 'error: XCTAssertEqual failed'),
            ('test-timeout', 'Test execution timed out'),
            ('test-build', '** TEST BUILD FAILED **'),
            ('unknown', 'error: unrecognized diagnostic'),
            ('unknown', None),
            ('unknown', 'unreadable'),
        ]
        for category, diagnostic in fixtures:
            with self.subTest(category=category, diagnostic=diagnostic), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, logs = root / 'source', root / 'logs'
                source.mkdir()
                original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key')
                process = MagicMock()
                process.poll.return_value = None
                kill_required = category == 'simulator-startup-communication'
                if kill_required:
                    process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
                devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [{'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                def stage(label, args, cwd, output):
                    if label == 'app-tests':
                        if diagnostic is not None:
                            (output / 'app-tests.log').write_text(
                                diagnostic + '\nPRIVATE_SOURCE_CANARY sk-fake-key /private/source/Hidden.swift testPrivate\n')
                        print('app-tests: failed', flush=True)
                        raise original

                public, stderr = io.StringIO(), io.StringIO()
                read_failure = failing_log_reads(OSError('PRIVATE_SOURCE_CANARY sk-fake-key')) if diagnostic == 'unreadable' else contextlib.nullcontext()
                with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'), patch.object(helper, 'classify_app_tests_failure', wraps=helper.classify_app_tests_failure) as classify, read_failure:
                    with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                        with self.assertRaises(helper.StageFailed) as caught:
                            helper.run_profile('ios', source, logs)
                self.assertIs(caught.exception, original)
                self.assertEqual(public.getvalue(), f'app-tests: failed\napp-tests-{category}: failed\n')
                self.assertEqual(stderr.getvalue(), '')
                if category == 'test-build':
                    self.assertEqual(classify.call_count, 2)
                    self.assertEqual(classify.call_args.kwargs, {'build_reason': True})
                else:
                    classify.assert_called_once_with(logs / 'app-tests.log')
                process.terminate.assert_called_once_with()
                self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})
                if kill_required:
                    process.kill.assert_called_once_with()
                    self.assertEqual(process.wait.call_count, 2)
                else:
                    process.kill.assert_not_called()
                    process.wait.assert_called_once_with(timeout=10)

    def test_classifier_memory_error_is_silent_unknown(self):
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            log.write_text('error: cannot find fixture in scope')
            public = io.StringIO()
            with failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY')):
                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                    self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
            self.assertEqual(public.getvalue(), '')

    def test_diagnostic_failures_never_replace_original_stage_failure(self):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        for fault in ['read-memory', 'classifier-memory', 'classifier-stage', 'output-write', 'output-flush']:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate')
                process = MagicMock()
                process.poll.return_value = None
                process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
                devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                    {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                def stage(label, args, cwd, logs):
                    if label == 'app-tests':
                        (logs / 'app-tests.log').write_text('error: XCTAssertEqual failed: PRIVATE_SOURCE_CANARY')
                        print('app-tests: failed', flush=True)
                        raise original

                class FaultyOutput(io.StringIO):
                    def write(self, text):
                        if fault == 'output-write' and text.startswith('app-tests-'):
                            raise OSError('PRIVATE_SOURCE_CANARY')
                        return super().write(text)

                    def flush(self):
                        if fault == 'output-flush' and 'app-tests-test-assertion' in self.getvalue():
                            raise MemoryError('PRIVATE_SOURCE_CANARY')
                        return super().flush()

                public, stderr = FaultyOutput(), io.StringIO()
                diagnostic_fault = contextlib.nullcontext()
                if fault == 'read-memory':
                    diagnostic_fault = failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY'))
                elif fault.startswith('classifier-'):
                    error = MemoryError if fault == 'classifier-memory' else helper.StageFailed
                    diagnostic_fault = patch.object(helper, 'classify_app_tests_failure', side_effect=error('PRIVATE_SOURCE_CANARY'))
                with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'), diagnostic_fault:
                    with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                        with self.assertRaises(helper.StageFailed) as caught:
                            helper.run_profile('ios', root, root / 'logs')
                self.assertIs(caught.exception, original)
                expected = 'app-tests: failed\n'
                if fault != 'output-write':
                    category = 'unknown' if fault in ['read-memory', 'classifier-memory', 'classifier-stage'] else 'test-assertion'
                    expected += f'app-tests-{category}: failed\n'
                self.assertEqual(public.getvalue(), expected)
                self.assertEqual(stderr.getvalue(), '')
                process.terminate.assert_called_once_with()
                self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})
                process.kill.assert_called_once_with()
                self.assertEqual(process.wait.call_count, 2)
                self.assertEqual(process.wait.call_args_list[1].args, ())
                self.assertEqual(process.wait.call_args_list[1].kwargs, {})

    def test_adversarial_diagnostics_finish_in_isolated_processes_and_cleanup(self):
        self.assert_diagnostic_pressure('base')

    def test_build_reason_pressure_finishes_before_cleanup_deadline(self):
        self.assert_diagnostic_pressure('build')

    def assert_diagnostic_pressure(self, mode):
        import subprocess
        import textwrap
        child = textwrap.dedent('''\
            import contextlib
            import io
            import json
            from pathlib import Path
            import sys
            import tempfile
            from unittest.mock import MagicMock, patch
            from scripts import private_build as helper

            limit = 1024 * 1024
            patterns = {
                'error-prefix': 'error: ',
                'assert-prefix': 'XCTAssertEqual ',
                'xctest-prefix': 'error: -[',
                'scope-prefix': 'error: cannot find ',
                'many-long-lines': ('error: -[XCTAssertEqual error: cannot find ' * 99) + '\\n',
                'many-short-lines': 'error: \\n',
            }
            pattern = patterns[sys.argv[1]]
            summary = '\\n** BUILD FAILED **\\n' if sys.argv[2] == 'build' else ''
            expected = 'test-build' if summary else 'unknown'
            diagnostic = pattern * ((limit - len(summary)) // len(pattern)) + summary
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                log = root / 'app-tests.log'
                log.write_text(diagnostic)
                public, stderr = io.StringIO(), io.StringIO()
                original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate')
                process = MagicMock()
                process.poll.return_value = None
                process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
                devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                    {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                def stage(label, *args):
                    if label == 'app-tests':
                        print('app-tests: failed', flush=True)
                        raise original

                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                    assert helper.classify_app_tests_failure(log) == expected
                    assert helper.classify_app_tests_failure(log, build_reason=True) == expected
                    with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'):
                        try:
                            helper.run_profile('ios', root, root)
                        except helper.StageFailed as caught:
                            assert caught is original
                        else:
                            raise AssertionError('business failure was swallowed')
                assert public.getvalue() == 'app-tests: failed\\napp-tests-' + expected + ': failed\\n'
                assert stderr.getvalue() == ''
                process.terminate.assert_called_once_with()
                assert process.wait.call_args_list[0].kwargs == {'timeout': 10}
                process.kill.assert_called_once_with()
                assert process.wait.call_count == 2
                print('unknown')
        ''')
        for case in ['error-prefix', 'assert-prefix', 'xctest-prefix', 'scope-prefix',
                     'many-long-lines', 'many-short-lines']:
            with self.subTest(case=case):
                try:
                    result = subprocess.run([sys.executable, '-c', child, case, mode],
                                            cwd=Path(__file__).parents[1],
                                            capture_output=True, text=True, timeout=10)
                except subprocess.TimeoutExpired:
                    self.fail('Diagnostic stress subprocess exceeded 10 seconds')
                self.assertEqual(result.returncode, 0, 'Diagnostic stress subprocess failed')
                self.assertEqual(result.stdout, 'unknown\n')
                self.assertEqual(result.stderr, '')

    def test_bounded_read_and_stream_failures_are_silent_unknown(self):
        from unittest.mock import MagicMock
        helper = importlib.import_module('scripts.private_build')
        limit = 1024 * 1024
        for fault in ['read-memory', 'read-oserror', 'close-memory']:
            with self.subTest(fault=fault):
                log = MagicMock()
                stream = log.open.return_value.__enter__.return_value
                stream.read.return_value = b'error: cannot find fixture in scope'
                if fault == 'close-memory':
                    log.open.return_value.__exit__.side_effect = MemoryError('PRIVATE_SOURCE_CANARY')
                else:
                    error = MemoryError if fault == 'read-memory' else OSError
                    stream.read.side_effect = error('PRIVATE_SOURCE_CANARY')
                public = io.StringIO()
                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                    self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
                log.open.assert_called_once_with('rb')
                stream.read.assert_called_once_with(limit + 1)
                self.assertEqual(public.getvalue(), '')

    def test_oversized_logs_are_unknown_instead_of_classifying_a_truncated_prefix(self):
        helper = importlib.import_module('scripts.private_build')
        limit = 1024 * 1024
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            prefix = b'error: cannot find fixture in scope\n'
            log.write_bytes(prefix + b' ' * (limit - len(prefix)))
            self.assertEqual(helper.classify_app_tests_failure(log), 'compile')
            with log.open('ab') as output:
                output.write(b' ')
            self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
            with log.open('wb') as output:
                output.write(prefix)
                output.seek(64 * limit)
                output.write(b'error: XCTAssertEqual failed: PRIVATE_SOURCE_CANARY')
            public = io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                self.assertEqual(helper.classify_app_tests_failure(log), 'unknown')
            self.assertEqual(public.getvalue(), '')

    def test_classifier_hook_is_absent_on_success_and_non_app_stage_failures(self):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        for failure in [None, 'swift-package-tests', 'ui-api-readiness', 'non-stage-app-error']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                process = MagicMock()
                process.poll.return_value = 1 if failure == 'ui-api-readiness' else None
                devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [{'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                def stage(label, *args):
                    if label == failure:
                        raise helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key')
                    if label == 'app-tests' and failure == 'non-stage-app-error':
                        raise OSError('PRIVATE_SOURCE_CANARY sk-fake-key')

                public = io.StringIO()
                with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process) as spawn, patch.object(helper.urllib.request, 'urlopen'), patch.object(helper, 'classify_app_tests_failure') as classify, contextlib.redirect_stdout(public), contextlib.redirect_stderr(public):
                    if failure is None:
                        helper.run_profile('ios', root, root / 'logs')
                    else:
                        error = OSError if failure == 'non-stage-app-error' else helper.StageFailed
                        with self.assertRaises(error):
                            helper.run_profile('ios', root, root / 'logs')
                classify.assert_not_called()
                self.assertEqual(public.getvalue(), '')
                if failure == 'swift-package-tests':
                    spawn.assert_not_called()
                else:
                    process.terminate.assert_called_once_with()
                    process.wait.assert_called_once_with(timeout=10)

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


    def test_ios_mock_and_baked_app_address_use_fixed_isolated_port(self):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            process = MagicMock()
            process.poll.return_value = None
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [{'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}
            with patch.object(helper, 'run_stage') as run, patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process) as spawn, patch.object(helper.urllib.request, 'urlopen') as health:
                helper.run_profile('ios', source, root / 'logs')
            self.assertEqual(spawn.call_args.kwargs.get('env', {}).get('PORT'), '18788')
            self.assertEqual(health.call_args.args[0], 'http://127.0.0.1:18788/v0/health')
            args = run.call_args_list[-1].args[1]
            self.assertIn('OPENSTELLA_API_BASE_URL=http://127.0.0.1:18788', args)
            self.assertIn('OPENSTELLA_ALLOWS_ARBITRARY_LOADS=YES', args)
            process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
