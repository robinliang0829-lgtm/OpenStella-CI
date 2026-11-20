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

    def test_assertion_detail_true(self):
        self.assert_assertion_detail('error: XCTAssertTrue failed - PRIVATE_SOURCE_CANARY', 'true')

    def test_assertion_detail_located_xctest_groups(self):
        for directory, target in [('FixtureTests', 'hosted-tests'), ('FixtureUITests', 'ui-tests')]:
            with self.subTest(target=target):
                self.assert_assertion_detail(
                    f'/private/{directory}/Hidden.swift:12: error: -[Fixture.Class testPrivate] : XCTAssertTrue failed - PRIVATE_SOURCE_CANARY',
                    'true', target)

    def test_assertion_detail_basic_kinds(self):
        for kind, body in [
            ('false', 'XCTAssertFalse failed - PRIVATE_SOURCE_CANARY'),
            ('equal', 'XCTAssertEqual failed: ("PRIVATE_SOURCE_CANARY") is not equal to ("fixture") - sk-fake-key'),
            ('unwrap', 'XCTUnwrap failed: expected non-nil value of type String - PRIVATE_SOURCE_CANARY'),
        ]:
            with self.subTest(kind=kind):
                self.assert_assertion_detail('error: -[Fixture.Class testPrivate] : ' + body, kind)

    def test_assertion_detail_wait_timeout_from_equality_operands_only(self):
        for result_type in ['XCTWaiterResult', 'XCTWaiter.Result']:
            with self.subTest(result_type=result_type):
                self.assert_assertion_detail(
                    '/private/FixtureUITests/Hidden.swift:12:3: error: -[Fixture.Class testPrivate] : '
                    f'XCTAssertEqual failed: ("{result_type}(rawValue: 2)") is not equal to ("{result_type}(rawValue: 1)") - PRIVATE_SOURCE_CANARY',
                    'wait-timeout', 'ui-tests')

    def test_assertion_detail_references_do_not_add_kind_or_group(self):
        line = '/private/FixtureUITests/Hidden.swift:12:3: error: -[Fixture.Class testPrivate] : XCTAssertTrue failed - PRIVATE_SOURCE_CANARY'
        for diagnostic in [
            'expected text: ' + line, '> ' + line, '"' + line + '"', "'" + line + "'",
            'warning: ' + line, 'note: ' + line,
            line.replace(': error: ', ': warning: '), line.replace(': error: ', ': note: '),
            line.replace(':12:3:', ':not-line:3:'),
            'error: expected text: XCTAssertTrue failed - PRIVATE_SOURCE_CANARY',
            'error: -[Fixture.Class testPrivate] : "XCTAssertTrue failed - quoted"',
            'error: -[Fixture.Class testPrivate] : failed - quoted XCTAssertTrue failed - fixture',
            '> ' + line + '\n' + line,
        ]:
            with self.subTest(diagnostic=diagnostic):
                self.assert_assertion_detail(diagnostic)

    def test_assertion_detail_same_line_groups_and_kind_matrix(self):
        for kind, body in [('true', 'XCTAssertTrue failed - fixture'),
                           ('false', 'XCTAssertFalse failed - fixture'),
                           ('equal', 'XCTAssertEqual failed: ("1") is not equal to ("0")'),
                           ('unwrap', 'XCTUnwrap failed: expected non-nil value')]:
            for directory, target in [('FixtureTests', 'hosted-tests'),
                                      ('FixtureUITests', 'ui-tests'), ('Unknown', 'unknown')]:
                with self.subTest(kind=kind, target=target):
                    self.assert_assertion_detail(
                        f'/private/{directory}/Hidden.swift:12:3: error: -[Fixture.Class testPrivate] : {body}',
                        kind, target)
        for diagnostic in [
            'error: -[FixtureUITests.Class testPrivate] : XCTAssertTrue failed - fixture',
            '/private/FixtureUITests/Hidden.swift:12:3: warning: unrelated\nerror: XCTAssertTrue failed - fixture',
            'CompileSwift /private/FixtureUITests/Hidden.swift\nerror: XCTAssertTrue failed - fixture',
            'error: XCTAssertTrue failed - /private/FixtureUITests/Hidden.swift:12:3',
            '/private/FixtureUITests.swift:12: error: XCTAssertTrue failed - fixture',
            '/private/FixtureUITestsHelper/Hidden.swift:12: error: XCTAssertTrue failed - fixture',
            '/private/FixtureTests/FixtureUITests/Hidden.swift:12: error: XCTAssertTrue failed - fixture',
            '/private/FixtureUITests/../Unknown/Hidden.swift:12: error: XCTAssertTrue failed - fixture',
            'error: XCTAssertTrue failed - fixture\n/private/FixtureUITests/Hidden.swift:12: error: XCTAssertFalse failed - fixture',
        ]:
            with self.subTest(diagnostic=diagnostic):
                self.assert_assertion_detail(diagnostic, 'true')

    def test_assertion_detail_waiter_words_or_numeric_values_are_not_wait_timeout(self):
        wait = 'XCTAssertEqual failed: ("XCTWaiterResult(rawValue: 2)") is not equal to ("XCTWaiterResult(rawValue: 1)")'
        for body, kind in [
            ('XCTAssertTrue failed - quoted ' + wait, 'true'),
            ('XCTAssertFalse failed - quoted ' + wait, 'false'),
            ('XCTUnwrap failed: expected non-nil - quoted ' + wait, 'unwrap'),
            ('XCTAssertEqual failed: ("2") is not equal to ("1") - quoted ' + wait, 'equal'),
            ('XCTAssertEqual failed: quoted "' + wait + '"', 'equal'),
            (wait.replace('rawValue: 2', 'rawValue: 3'), 'equal'),
            (wait.replace('rawValue: 1', 'rawValue: 2'), 'equal'),
            (wait + ' trailing unexpected text', 'equal'),
        ]:
            with self.subTest(kind=kind, body=body):
                self.assert_assertion_detail('error: -[Fixture.Class testPrivate] : ' + body, kind)

    def test_assertion_detail_faults_are_best_effort(self):
        for fault in ['read', 'classifier', 'write', 'flush', 'unallowlisted']:
            with self.subTest(fault=fault):
                self.assert_assertion_detail(
                    '/private/FixtureTests/Hidden.swift:12: error: XCTAssertTrue failed - PRIVATE_SOURCE_CANARY',
                    'true', 'hosted-tests', fault=fault)

    def test_assertion_detail_reads_are_bounded_silent_and_fail_closed(self):
        from unittest.mock import MagicMock
        helper = importlib.import_module('scripts.private_build')
        limit = 1024 * 1024
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            public, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                self.assertEqual(helper.classify_app_tests_failure(log, assertion_detail=True), 'unknown')
                prefix = b'error: XCTAssertTrue failed - PRIVATE_SOURCE_CANARY\n'
                log.write_bytes(prefix + b' ' * (limit - len(prefix)))
                self.assertEqual(helper.classify_app_tests_failure(log, assertion_detail=True),
                                 'assertion-true-target-unknown')
                with log.open('ab') as stream:
                    stream.write(b' ')
                self.assertEqual(helper.classify_app_tests_failure(log, assertion_detail=True), 'unknown')
                log.write_bytes(b'\xff' + prefix)
                self.assertEqual(helper.classify_app_tests_failure(log, assertion_detail=True), 'unknown')
                for error in [OSError('PRIVATE_SOURCE_CANARY'), MemoryError('PRIVATE_SOURCE_CANARY')]:
                    with failing_log_reads(error):
                        self.assertEqual(helper.classify_app_tests_failure(log, assertion_detail=True), 'unknown')
                mocked = MagicMock()
                stream = mocked.open.return_value.__enter__.return_value
                stream.read.return_value = prefix
                self.assertEqual(helper.classify_app_tests_failure(mocked, assertion_detail=True),
                                 'assertion-true-target-unknown')
                stream.read.assert_called_once_with(limit + 1)
                mocked.open.return_value.__exit__.side_effect = MemoryError('PRIVATE_SOURCE_CANARY')
                self.assertEqual(helper.classify_app_tests_failure(mocked, assertion_detail=True), 'unknown')
            self.assertEqual(public.getvalue(), '')
            self.assertEqual(stderr.getvalue(), '')

    def assert_assertion_detail(self, diagnostic, kind=None, target='unknown', *, fault=None):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key testPrivate')
            process = MagicMock()
            process.poll.return_value = None
            process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

            def stage(label, args, cwd, logs):
                if label == 'app-tests':
                    (logs / 'app-tests.log').write_text(diagnostic + '\nPRIVATE_SOURCE_CANARY sk-fake-key\n')
                    print('app-tests: failed', flush=True)
                    raise original

            real_classifier = helper.classify_app_tests_failure
            def classify(log, **kwargs):
                if kwargs.get('assertion_detail'):
                    if fault == 'read':
                        with failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY')):
                            return real_classifier(log, **kwargs)
                    if fault == 'classifier':
                        raise MemoryError('PRIVATE_SOURCE_CANARY')
                    if fault == 'unallowlisted':
                        return 'PRIVATE_SOURCE_CANARY sk-fake-key testPrivate'
                return real_classifier(log, **kwargs)

            class Output(io.StringIO):
                def write(self, text):
                    if fault == 'write' and 'app-tests-assertion-' in text:
                        raise OSError('PRIVATE_SOURCE_CANARY')
                    return super().write(text)
                def flush(self):
                    if fault == 'flush' and 'app-tests-assertion-' in self.getvalue():
                        raise MemoryError('PRIVATE_SOURCE_CANARY')
                    return super().flush()

            public, stderr = Output(), io.StringIO()
            with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'), patch.object(helper, 'classify_app_tests_failure', side_effect=classify) as classified:
                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(helper.StageFailed) as caught:
                        helper.run_profile('ios', root, root / 'logs')
            self.assertIs(caught.exception, original)
            expected = 'app-tests: failed\napp-tests-test-assertion: failed\n'
            if kind and fault in (None, 'flush'):
                expected += f'app-tests-assertion-{kind}-target-{target}: failed\n'
            self.assertEqual(public.getvalue(), expected)
            self.assertEqual(stderr.getvalue(), '')
            self.assertEqual(classified.call_count, 2)
            self.assertEqual(classified.call_args.kwargs, {'assertion_detail': True})
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_count, 2)
            self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})
            self.assertEqual(process.wait.call_args_list[1].args, ())
            self.assertEqual(process.wait.call_args_list[1].kwargs, {})

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

    def test_type_inference_detail_complexity(self):
        self.assert_type_inference_detail(
            'error: the compiler is unable to type-check this expression in reasonable time',
            'typecheck-complexity', 'unknown')

    def test_type_inference_detail_ambiguous(self):
        self.assert_type_inference_detail(
            'error: ambiguous use of PRIVATE_SOURCE_CANARY', 'ambiguous-use')

    def test_type_inference_detail_generic(self):
        self.assert_type_inference_detail(
            'error: generic parameter PRIVATE_SOURCE_CANARY could not be inferred', 'generic-inference')

    def test_type_inference_detail_app_target(self):
        self.assert_type_inference_detail(
            '/private/ios/OpenStella/Features/Hidden.swift:12:3: error: ambiguous use of PRIVATE_SOURCE_CANARY',
            'ambiguous-use', 'app')

    def test_type_inference_detail_ui_target(self):
        self.assert_type_inference_detail(
            '/private/ios/FixtureUITests/Hidden.swift:12:3: error: generic parameter Fixture could not be inferred',
            'generic-inference', 'ui-tests')

    def test_type_inference_detail_hosted_target(self):
        self.assert_type_inference_detail(
            '/private/ios/FixtureTests/Hidden.swift:12:3: error: unable to type-check this expression in reasonable time',
            'typecheck-complexity', 'hosted-tests')

    def test_type_inference_detail_matrix_is_fixed_and_same_line_only(self):
        messages = [
            ('typecheck-complexity', 'the compiler is unable to type-check this expression in reasonable time'),
            ('typecheck-complexity', 'unable to type-check this expression in reasonable time'),
            ('ambiguous-use', 'ambiguous use of PRIVATE_SOURCE_CANARY'),
            ('generic-inference', 'generic parameter PRIVATE_SOURCE_CANARY could not be inferred'),
        ]
        for kind, message in messages:
            for directory, target in [('ios/OpenStella/Features', 'app'),
                                      ('ios/FixtureTests', 'hosted-tests'),
                                      ('ios/FixtureUITests', 'ui-tests'),
                                      ('Unrecognized', 'unknown')]:
                with self.subTest(kind=kind, target=target):
                    self.assert_type_inference_detail(
                        f'/private/{directory}/Hidden.swift:12:3: error: {message}', kind, target)
        for diagnostic in [
            '/private/ios/FixtureUITests/Hidden.swift:12:3: warning: unrelated\nerror: ambiguous use of fixture',
            '/private/ios/FixtureTests/Hidden.swift:12:3: error: unrelated\nerror: ambiguous use of fixture',
            'CompileSwift /private/ios/FixtureTests/Hidden.swift\nerror: ambiguous use of fixture',
            "error: ambiguous use of fixture /private/ios/FixtureUITests/Hidden.swift:12:3",
            '/private/FixtureTests.swift:12:3: error: ambiguous use of fixture',
            '/private/ios/OpenStellaUITestsHelper/Hidden.swift:12:3: error: ambiguous use of fixture',
            '/private/ios/OpenStella/../Unknown/Hidden.swift:12:3: error: ambiguous use of fixture',
            'error: ambiguous use of fixture\n/private/ios/FixtureUITests/Hidden.swift:12:3: error: generic parameter F could not be inferred',
        ]:
            with self.subTest(diagnostic=diagnostic):
                self.assert_type_inference_detail(diagnostic, 'ambiguous-use', 'unknown')
        self.assert_type_inference_detail(
            '/private/ios/FixtureUITests/Hidden.swift:12:3: error: ambiguous use of fixture\n'
            '/private/ios/FixtureTests/Hidden.swift:12:3: error: generic parameter F could not be inferred',
            'ambiguous-use', 'ui-tests')

    def test_type_inference_detail_references_are_not_errors(self):
        location = '/private/ios/FixtureUITests/Hidden.swift:12:3: error: ambiguous use of PRIVATE_SOURCE_CANARY'
        for diagnostic in ['expected text: ' + location, '> ' + location, '"' + location + '"',
                           "'" + location + "'", 'warning: ' + location, 'note: ' + location,
                           location.replace(': error: ', ': warning: error: '),
                           location.replace(': error: ', ': note: error: ')]:
            with self.subTest(diagnostic=diagnostic):
                self.assert_type_inference_detail(diagnostic)

    def test_type_inference_detail_faults_preserve_exception_and_cleanup(self):
        for fault in ['read', 'classifier', 'write', 'flush', 'unallowlisted']:
            with self.subTest(fault=fault):
                self.assert_type_inference_detail(
                    '/private/ios/FixtureTests/Hidden.swift:12:3: error: ambiguous use of PRIVATE_SOURCE_CANARY',
                    'ambiguous-use', 'hosted-tests', fault=fault)

    def test_type_inference_detail_is_bounded_silent_and_preserves_priority(self):
        from unittest.mock import MagicMock
        helper = importlib.import_module('scripts.private_build')
        limit = 1024 * 1024
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            public, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True), 'unknown')
                prefix = b'error: ambiguous use of PRIVATE_SOURCE_CANARY\n** BUILD FAILED **\n'
                log.write_bytes(prefix + b' ' * (limit - len(prefix)))
                self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True),
                                 'build-ambiguous-use-target-unknown')
                with log.open('ab') as output:
                    output.write(b' ')
                self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True), 'unknown')
                log.write_bytes(b'\xff' + prefix)
                self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True), 'unknown')
                for diagnostic, expected in [
                    ('error: ambiguous use of fixture', 'unknown'),
                    ('error: generic parameter fixture without required evidence\n** BUILD FAILED **', 'test-build'),
                    ('error: expected expression\nerror: ambiguous use of fixture\n** BUILD FAILED **', 'build-syntax-parser'),
                    ('error: XCTAssertEqual failed: error: ambiguous use of fixture\n** BUILD FAILED **', 'test-assertion'),
                    ('error: -[Fixture testPrivate] : XCTUnwrap failed: error: ambiguous use of fixture\n** BUILD FAILED **', 'test-assertion'),
                    ('Test execution timed out\nerror: ambiguous use of fixture\n** BUILD FAILED **', 'test-timeout'),
                ]:
                    with self.subTest(expected=expected):
                        log.write_text(diagnostic)
                        self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True), expected)
                for error in [OSError('PRIVATE_SOURCE_CANARY'), MemoryError('PRIVATE_SOURCE_CANARY')]:
                    with failing_log_reads(error):
                        self.assertEqual(helper.classify_app_tests_failure(log, type_inference_detail=True), 'unknown')
                stream_log = MagicMock()
                stream = stream_log.open.return_value.__enter__.return_value
                stream.read.return_value = prefix
                self.assertEqual(helper.classify_app_tests_failure(stream_log, type_inference_detail=True),
                                 'build-ambiguous-use-target-unknown')
                stream.read.assert_called_once_with(limit + 1)
                stream_log.open.return_value.__exit__.side_effect = MemoryError('PRIVATE_SOURCE_CANARY')
                self.assertEqual(helper.classify_app_tests_failure(stream_log, type_inference_detail=True), 'unknown')
            self.assertEqual(public.getvalue(), '')
            self.assertEqual(stderr.getvalue(), '')

    def assert_type_inference_detail(self, diagnostic, subtype=None, target='unknown', *, fault=None):
        from unittest.mock import patch, MagicMock
        import json
        helper = importlib.import_module('scripts.private_build')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate')
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

            real_classifier = helper.classify_app_tests_failure
            def classify(log, **kwargs):
                if kwargs.get('type_inference_detail'):
                    if fault == 'read':
                        with failing_log_reads(MemoryError('PRIVATE_SOURCE_CANARY')):
                            return real_classifier(log, **kwargs)
                    if fault == 'classifier':
                        raise helper.StageFailed('PRIVATE_DIAGNOSTIC_FAILURE')
                    if fault == 'unallowlisted':
                        return 'PRIVATE_SOURCE_CANARY sk-fake-key /private/Hidden testPrivate'
                return real_classifier(log, **kwargs)

            class Output(io.StringIO):
                def write(self, text):
                    if fault == 'write' and '-target-' in text:
                        raise OSError('PRIVATE_SOURCE_CANARY')
                    return super().write(text)
                def flush(self):
                    if fault == 'flush' and '-target-' in self.getvalue():
                        raise MemoryError('PRIVATE_SOURCE_CANARY')
                    return super().flush()

            public, stderr = Output(), io.StringIO()
            with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'), patch.object(helper, 'classify_app_tests_failure', side_effect=classify):
                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(helper.StageFailed) as caught:
                        helper.run_profile('ios', root, root / 'logs')
            self.assertIs(caught.exception, original)
            expected = 'app-tests: failed\napp-tests-test-build: failed\n'
            if subtype:
                expected += 'app-tests-build-type-inference: failed\n'
                if fault in (None, 'flush'):
                    expected += f'app-tests-build-{subtype}-target-{target}: failed\n'
            self.assertEqual(public.getvalue(), expected)
            self.assertEqual(stderr.getvalue(), '')
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_count, 2)
            self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 10})
            self.assertEqual(process.wait.call_args_list[1].args, ())
            self.assertEqual(process.wait.call_args_list[1].kwargs, {})

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
                    detailed = reason == 'type-inference' and fault is None
                    if detailed:
                        expected += 'app-tests-build-typecheck-complexity-target-unknown: failed\n'
                    self.assertEqual(public.getvalue(), expected)
                    self.assertEqual(stderr.getvalue(), '')
                    self.assertEqual(classified.call_count, 3 if detailed else 2)
                    self.assertEqual(classified.call_args.kwargs,
                                     {'type_inference_detail': True} if detailed else {'build_reason': True})
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
                elif category == 'test-assertion':
                    self.assertEqual(classify.call_count, 2)
                    self.assertEqual(classify.call_args.kwargs, {'assertion_detail': True})
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

    def test_type_inference_detail_pressure_finishes_before_cleanup_deadline(self):
        self.assert_diagnostic_pressure('detail')

    def test_assertion_detail_pressure_finishes_before_cleanup_deadline(self):
        self.assert_diagnostic_pressure('assertion')

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
                'location-prefix': '/private/Fixture.swift:12:3: error: ',
                'many-locations': '/private/FixtureTests/Hidden.swift:12:3: error: unknown\\n',
                'deep-location': '/d',
                'assert-body': 'error: XCTAssertTrue failed - ',
                'assert-repeated': 'XCTAssertTrue ',
                'assert-wrapper': 'error: -[',
                'assert-deep-path': '/d',
            }
            pattern = patterns[sys.argv[1]]
            summary = '\\n** BUILD FAILED **\\n' if sys.argv[2] in ('build', 'detail') else ''
            expected = 'test-build' if summary else 'unknown'
            reason, detail = expected, expected
            if sys.argv[1] == 'deep-location':
                summary = '/ios/FixtureTests/Hidden.swift:12:3: error: ambiguous use of PRIVATE_SOURCE_CANARY' + summary
                reason = 'build-type-inference'
                detail = 'build-ambiguous-use-target-hosted-tests'
            assertion_expected = expected
            if sys.argv[1] in ('assert-body', 'assert-repeated', 'assert-wrapper', 'assert-deep-path'):
                expected = reason = 'test-assertion'
                assertion_expected = 'unknown'
                summary = 'failed - PRIVATE_SOURCE_CANARY'
                if sys.argv[1] == 'assert-body':
                    assertion_expected = 'assertion-true-target-unknown'
                elif sys.argv[1] == 'assert-wrapper':
                    summary = 'XCTAssertTrue failed - PRIVATE_SOURCE_CANARY'
                elif sys.argv[1] == 'assert-deep-path':
                    summary = '/FixtureUITests/Hidden.swift:12: error: -[Fixture.Class testPrivate] : XCTAssertTrue failed - PRIVATE_SOURCE_CANARY'
                    assertion_expected = 'assertion-true-target-ui-tests'
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
                    assert helper.classify_app_tests_failure(log, build_reason=True) == reason
                    if sys.argv[2] == 'detail':
                        assert helper.classify_app_tests_failure(log, type_inference_detail=True) == detail
                    if sys.argv[2] == 'assertion':
                        assert helper.classify_app_tests_failure(log, assertion_detail=True) == assertion_expected
                    with patch.object(helper, 'run_stage', side_effect=stage), patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), patch.object(helper.subprocess, 'Popen', return_value=process), patch.object(helper.urllib.request, 'urlopen'):
                        try:
                            helper.run_profile('ios', root, root)
                        except helper.StageFailed as caught:
                            assert caught is original
                        else:
                            raise AssertionError('business failure was swallowed')
                expected_public = 'app-tests: failed\\napp-tests-' + expected + ': failed\\n'
                if sys.argv[1] == 'deep-location':
                    expected_public += 'app-tests-build-type-inference: failed\\napp-tests-build-ambiguous-use-target-hosted-tests: failed\\n'
                if sys.argv[2] == 'assertion' and assertion_expected.startswith('assertion-'):
                    expected_public += 'app-tests-' + assertion_expected + ': failed\\n'
                assert public.getvalue() == expected_public
                assert stderr.getvalue() == ''
                process.terminate.assert_called_once_with()
                assert process.wait.call_args_list[0].kwargs == {'timeout': 10}
                process.kill.assert_called_once_with()
                assert process.wait.call_count == 2
                print('unknown')
        ''')
        cases = ['error-prefix', 'assert-prefix', 'xctest-prefix', 'scope-prefix',
                 'many-long-lines', 'many-short-lines']
        if mode == 'detail':
            cases += ['location-prefix', 'many-locations', 'deep-location']
        elif mode == 'assertion':
            cases += ['assert-body', 'assert-repeated', 'assert-wrapper', 'assert-deep-path']
        for case in cases:
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


class OriginProtocolTests(unittest.TestCase):
    maxDiff = None
    failures = ('truth', 'equality', 'wait-timeout', 'unwrap', 'other')
    areas = ('bootstrap', 'onboarding', 'layout', 'navigation', 'image-check', 'other')

    def matrix(self):
        return [(f'OPE12_ORIGIN_V1 failure={failure} area={area}',
                 f'app-tests-origin-{failure}-area-{area}: failed')
                for failure in self.failures for area in self.areas]

    def assert_origin_run(self, diagnostic, statuses=(), *, category='test-assertion', fault=None):
        from unittest.mock import MagicMock, patch
        import json
        from scripts import private_build as helper
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = helper.StageFailed('PRIVATE_SOURCE_CANARY sk-fake-key testCounterfeit')
            process = MagicMock()
            process.poll.return_value = None
            process.wait.side_effect = [helper.subprocess.TimeoutExpired('fixture', 10), None]
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

            def stage(label, *args):
                if label == 'app-tests':
                    (root / 'logs/app-tests.log').write_text(diagnostic)
                    print('app-tests: failed', flush=True)
                    raise original

            class Output(io.StringIO):
                def write(self, text):
                    if fault == 'write' and text.startswith('app-tests-origin-'):
                        raise OSError('PRIVATE_SOURCE_CANARY')
                    return super().write(text)

                def flush(self):
                    if fault == 'flush' and 'app-tests-origin-' in self.getvalue():
                        raise MemoryError('PRIVATE_SOURCE_CANARY')
                    return super().flush()

            public, stderr = Output(), io.StringIO()
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(helper, 'run_stage', side_effect=stage))
                stack.enter_context(patch.object(helper.subprocess, 'check_output',
                                                side_effect=['26.2', json.dumps(devices)]))
                stack.enter_context(patch.object(helper.subprocess, 'Popen', return_value=process))
                stack.enter_context(patch.object(helper.urllib.request, 'urlopen'))
                if fault in ('reader', 'unallowlisted'):
                    options = ({'side_effect': MemoryError('PRIVATE_SOURCE_CANARY')}
                               if fault == 'reader' else {'return_value': ('PRIVATE_SOURCE_CANARY',)})
                    stack.enter_context(patch.object(helper, 'app_tests_origin_statuses', **options))
                with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(helper.StageFailed) as caught:
                        helper.run_profile('ios', root, root / 'logs')
            self.assertIs(caught.exception, original)
            expected = f'app-tests: failed\napp-tests-{category}: failed\n'
            expected += ''.join(status + '\n' for status in statuses)
            self.assertEqual(public.getvalue(), expected)
            self.assertEqual(stderr.getvalue(), '')
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_args_list,
                             [unittest.mock.call(timeout=10), unittest.mock.call()])

    def checkpoint_matrix(self):
        return [(f'OPE12_CHECKPOINT_V1 checkpoint={checkpoint}',
                 f'app-tests-checkpoint-{checkpoint}: failed')
                for checkpoint in ('other', 'element-existence', 'accessibility-hit',
                                   'selected-state', 'frame-containment', 'scroll-reachability',
                                   'content-value', 'async-progress', 'request-isolation',
                                   'scene-identity',
                                   'guide-entry-hit', 'home-heading-hit', 'home-ready-wait',
                                   'home-tab-hit', 'host-ack-wait', 'inbound-return-hit',
                                   'inbound-return-wait', 'input-reflection-wait', 'item-slot-hit',
                                   'mailbox-hit', 'modal-close-hit', 'modal-dismiss-wait',
                                   'modal-ready-wait', 'onboarding-submit-hit', 'post-dismiss-tab-hit',
                                   'resident-chat-hit', 'scene-action-hit', 'scene-render-wait')]

    def test_checkpoint_role_literals_emit_complete_public_stdout(self):
        matrix = self.checkpoint_matrix()[10:]
        self.assertEqual(len(matrix), 18)
        for marker, status in matrix:
            with self.subTest(marker=marker):
                self.assert_origin_run('error: XCTAssertEqual failed\n' + marker, (status,))

    def test_checkpoint_role_allowlist_is_complete_unique_and_fixed_order(self):
        from scripts import private_build as helper
        matrix = self.matrix() + self.checkpoint_matrix()
        self.assertEqual(len(matrix), 58)
        self.assertEqual(len({marker for marker, _ in matrix}), 58)
        self.assertEqual(len({status for _, status in matrix}), 58)
        self.assertEqual(list(helper.APP_TESTS_ORIGIN_STATUSES.items()), matrix)
        diagnostic = ('error: XCTAssertEqual failed\n'
                      + '\r\n'.join(marker for marker, _ in reversed(matrix)) + '\r\n'
                      + '\n'.join(marker for marker, _ in matrix))
        self.assert_origin_run(diagnostic, [status for _, status in matrix])

    def test_checkpoint_role_quoted_suffix_whitespace_and_non_lf_boundaries_rejected(self):
        from scripts import private_build as helper
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for marker, _ in self.checkpoint_matrix()[10:]:
                invalid = ['"' + marker + '"', "'" + marker + "'", marker + 'suffix']
                for separator in (' ', '\t', '\v', '\f', '\r', '\r\r', '\x85',
                                  '\u00a0', '\u2028', '\u2029'):
                    invalid.extend((separator + marker, marker + separator,
                                    'quoted:' + separator + marker + separator + 'end quote'))
                for specimen in invalid:
                    with self.subTest(marker=marker, specimen=repr(specimen)):
                        diagnostic = 'error: XCTAssertEqual failed\n' + specimen
                        log.write_bytes(diagnostic.encode('utf-8'))
                        self.assertEqual(helper.app_tests_origin_statuses(log), ())
                        self.assert_origin_run(diagnostic)

    def test_origin_matrix_is_supplemental_deduplicated_and_fixed_order(self):
        matrix = self.matrix() + self.checkpoint_matrix()
        self.assertEqual(len(matrix), 58)
        diagnostic = ('error: XCTAssertEqual failed\nPRIVATE_SOURCE_CANARY sk-fake-key\n'
                      + '\r\n'.join(marker for marker, _ in reversed(matrix)) + '\r\n'
                      + '\n'.join(marker for marker, _ in matrix))
        self.assert_origin_run(diagnostic, [status for _, status in matrix])

    def test_checkpoint_individual_literals_emit_complete_public_stdout(self):
        # A checkpoint is fixed semantics, not a testcase/path association.
        # The trusted callback supplies truth-only V1 provenance; the reader
        # deliberately does not infer a relationship from neighboring lines.
        for marker, status in self.checkpoint_matrix():
            with self.subTest(marker=marker):
                self.assert_origin_run('error: XCTAssertEqual failed\n' + marker, (status,))

    def test_checkpoint_exact_boundaries_and_unknown_values(self):
        from scripts import private_build as helper
        separators = ('\r', '\r\r', '\v', '\f', '\x1c', '\x1d', '\x1e',
                      '\x85', '\u2028', '\u2029')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for marker, status in self.checkpoint_matrix():
                for ending in ('', '\n', '\r\n'):
                    with self.subTest(marker=marker, ending=repr(ending)):
                        diagnostic = 'error: XCTAssertEqual failed\n' + marker + ending
                        log.write_bytes(diagnostic.encode('utf-8'))
                        self.assertEqual(helper.app_tests_origin_statuses(log), (status,))
                        self.assert_origin_run(diagnostic, (status,))
                invalid = [
                    ' ' + marker, '\t' + marker, marker + ' ', marker + '\t',
                    marker + ' PRIVATE_SOURCE_CANARY', marker + 'suffix',
                    '"' + marker + '"', "'" + marker + "'", '> ' + marker,
                    'error: ' + marker, 'expected text: ' + marker,
                    'PRIVATE_SOURCE_CANARY' + marker, marker + '\x00',
                    marker.replace('checkpoint=', 'checkpoint=unknown-'),
                    marker.replace('OPE12_CHECKPOINT_V1', 'OPE12_CHECKPOINT_V2'),
                ]
                for separator in separators:
                    invalid.extend((separator + marker, marker + separator,
                                    'PRIVATE_PREFIX' + separator + marker,
                                    marker + separator + 'PRIVATE_SUFFIX',
                                    'quoted:' + separator + marker + separator + 'end quote',
                                    marker + separator + '\r\n'))
                for specimen in invalid:
                    with self.subTest(marker=marker, specimen=repr(specimen)):
                        diagnostic = 'error: XCTAssertEqual failed\n' + specimen
                        log.write_bytes(diagnostic.encode('utf-8'))
                        self.assertEqual(helper.app_tests_origin_statuses(log), ())
                        self.assert_origin_run(diagnostic)

    def test_checkpoint_requires_root_assertion_not_neighbor_origin(self):
        from unittest.mock import patch
        from scripts import private_build as helper
        for marker, _ in self.checkpoint_matrix():
            for diagnostic, category in (
                ('', 'unknown'), ('** BUILD FAILED **', 'test-build'),
                ('error: cannot find fixture in scope', 'compile'),
                ('Test execution timed out', 'test-timeout'),
            ):
                with self.subTest(marker=marker, category=category):
                    with patch.object(helper, 'app_tests_origin_statuses') as read:
                        self.assert_origin_run(diagnostic + '\n' + marker, category=category)
                    read.assert_not_called()

    def test_checkpoint_read_is_bounded_strict_utf8_and_silent(self):
        from unittest.mock import MagicMock
        from scripts import private_build as helper
        limit = 1024 * 1024
        public, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                for marker, status in self.checkpoint_matrix():
                    with self.subTest(marker=marker):
                        prefix = (marker + '\n').encode('utf-8')
                        log.write_bytes(prefix + b'x' * (limit - len(prefix)))
                        self.assertEqual(helper.app_tests_origin_statuses(log), (status,))
                        with log.open('ab') as output:
                            output.write(b'x')
                        self.assertEqual(helper.app_tests_origin_statuses(log), ())
                        log.write_bytes(prefix + b'\xff')
                        self.assertEqual(helper.app_tests_origin_statuses(log), ())
                        for error in (OSError('PRIVATE_SOURCE_CANARY'), MemoryError('PRIVATE_SOURCE_CANARY')):
                            with failing_log_reads(error):
                                self.assertEqual(helper.app_tests_origin_statuses(log), ())
                        mocked = MagicMock()
                        stream = mocked.open.return_value.__enter__.return_value
                        stream.read.return_value = prefix
                        self.assertEqual(helper.app_tests_origin_statuses(mocked), (status,))
                        mocked.open.assert_called_once_with('rb')
                        stream.read.assert_called_once_with(limit + 1)
                        mocked.open.return_value.__exit__.side_effect = MemoryError('PRIVATE_SOURCE_CANARY')
                        self.assertEqual(helper.app_tests_origin_statuses(mocked), ())
                log.unlink()
                self.assertEqual(helper.app_tests_origin_statuses(log), ())
        self.assertEqual(public.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')

    def test_checkpoint_faults_preserve_original_exception_and_cleanup(self):
        from unittest.mock import patch
        from scripts import private_build as helper
        for marker, status in self.checkpoint_matrix():
            for fault in ('reader', 'unallowlisted'):
                with self.subTest(marker=marker, fault=fault):
                    self.assert_origin_run('error: XCTAssertEqual failed\n' + marker, fault=fault)
            for fault in ('write', 'flush'):
                with self.subTest(marker=marker, fault=fault):
                    original_print = print

                    def faulty_print(*args, **kwargs):
                        if args and args[0] == status:
                            if fault == 'flush':
                                original_print(*args, **kwargs)
                            raise OSError('PRIVATE_SOURCE_CANARY')
                        return original_print(*args, **kwargs)

                    with patch('builtins.print', side_effect=faulty_print):
                        self.assert_origin_run('error: XCTAssertEqual failed\n' + marker,
                                               (status,) if fault == 'flush' else ())

    def test_checkpoint_reader_pressure_keeps_hard_ten_second_deadline(self):
        import signal
        from scripts import private_build as helper

        class DeadlineExpired(BaseException):
            pass

        def expire(*_):
            raise DeadlineExpired()

        matrix = self.matrix() + self.checkpoint_matrix()
        valid = '\n'.join(marker for marker, _ in reversed(matrix)) + '\n'
        patterns = (valid, self.checkpoint_matrix()[0][0] + 'suffix\n', 'x', '\n')
        limit = 1024 * 1024
        previous = signal.signal(signal.SIGALRM, expire)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / 'app-tests.log'
                for pattern in patterns:
                    with self.subTest(pattern_length=len(pattern)):
                        prefix = 'error: XCTAssertEqual failed\n'
                        diagnostic = prefix + pattern * ((limit - len(prefix)) // len(pattern))
                        expected = tuple(status for _, status in matrix) if pattern == valid else ()
                        log.write_bytes(diagnostic.encode('utf-8'))
                        self.assertEqual(helper.app_tests_origin_statuses(log), expected)
                        self.assert_origin_run(diagnostic, expected)
        except DeadlineExpired:
            self.fail('Checkpoint protocol stress exceeded 10 seconds')
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous)

    def test_origin_individual_literals_and_rejects_nonliteral_lines(self):
        from scripts import private_build as helper
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for marker, status in self.matrix():
                with self.subTest(marker=marker):
                    log.write_text(marker)
                    self.assertEqual(helper.app_tests_origin_statuses(log), (status,))
                    invalid = [
                        ' ' + marker, '\t' + marker, marker + ' ', marker + '\t',
                        marker + ' PRIVATE_SOURCE_CANARY', marker + 'suffix',
                        '"' + marker + '"', "'" + marker + "'", '> ' + marker,
                        'error: ' + marker, 'expected text: ' + marker,
                        'PRIVATE_SOURCE_CANARY' + marker, marker + '\x00',
                        marker.replace(' area=', '\narea='),
                        marker.replace('failure=', 'failure=unknown-'),
                        marker.replace('area=', 'area=unknown-'),
                        marker.replace('OPE12_ORIGIN_V1', 'OPE12_ORIGIN_V2'),
                    ]
                    log.write_text('\n'.join(invalid))
                    self.assertEqual(helper.app_tests_origin_statuses(log), ())
            self.assert_origin_run('error: XCTAssertEqual failed\n' + '\n'.join(invalid))

    def test_origin_only_lf_and_crlf_delimit_exact_protocol_lines(self):
        from scripts import private_build as helper
        separators = ('\r', '\r\r', '\v', '\f', '\x1c', '\x1d', '\x1e',
                      '\x85', '\u2028', '\u2029')
        marker, _ = self.matrix()[0]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for separator in separators:
                specimens = (
                    separator + marker,
                    marker + separator,
                    'PRIVATE_PREFIX' + separator + marker,
                    marker + separator + 'PRIVATE_SUFFIX',
                    'quoted text:' + separator + marker + separator + 'end quote',
                    marker + separator + '\r\n',
                )
                for specimen in specimens:
                    diagnostic = 'error: XCTAssertEqual failed\n' + specimen
                    for boundary in ('reader', 'public-run'):
                        with self.subTest(separator=repr(separator), specimen=repr(specimen),
                                          boundary=boundary):
                            if boundary == 'reader':
                                log.write_bytes(diagnostic.encode('utf-8'))
                                self.assertEqual(helper.app_tests_origin_statuses(log), ())
                            else:
                                self.assert_origin_run(diagnostic)

    def test_origin_lf_crlf_and_unterminated_lines_keep_existing_contract(self):
        from scripts import private_build as helper
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            for marker, status in self.matrix():
                for ending in ('', '\n', '\r\n'):
                    with self.subTest(marker=marker, ending=repr(ending)):
                        diagnostic = 'error: XCTAssertEqual failed\n' + marker + ending
                        log.write_bytes(diagnostic.encode('utf-8'))
                        self.assertEqual(helper.app_tests_origin_statuses(log), (status,))
                        self.assert_origin_run(diagnostic, (status,))
            matrix = self.matrix()
            diagnostic = ('error: XCTAssertEqual failed\r\n'
                          + '\n'.join(marker for marker, _ in matrix[:15]) + '\r\n'
                          + '\r\n'.join(marker for marker, _ in matrix[15:]))
            expected = tuple(status for _, status in matrix)
            log.write_bytes(diagnostic.encode('utf-8'))
            self.assertEqual(helper.app_tests_origin_statuses(log), expected)
            self.assert_origin_run(diagnostic, expected)

    def test_origin_read_is_bounded_silent_and_fails_closed(self):
        from unittest.mock import MagicMock
        from scripts import private_build as helper
        limit = 1024 * 1024
        marker, status = self.matrix()[0]
        prefix = (marker + '\n').encode()
        public, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'app-tests.log'
            with contextlib.redirect_stdout(public), contextlib.redirect_stderr(stderr):
                self.assertEqual(helper.app_tests_origin_statuses(log), ())
                log.write_bytes(prefix + b'x' * (limit - len(prefix)))
                self.assertEqual(helper.app_tests_origin_statuses(log), (status,))
                with log.open('ab') as output:
                    output.write(b'x')
                self.assertEqual(helper.app_tests_origin_statuses(log), ())
                log.write_bytes(prefix + b'\xff')
                self.assertEqual(helper.app_tests_origin_statuses(log), ())
                for error in (OSError('PRIVATE_SOURCE_CANARY'), MemoryError('PRIVATE_SOURCE_CANARY')):
                    with failing_log_reads(error):
                        self.assertEqual(helper.app_tests_origin_statuses(log), ())
                mocked = MagicMock()
                stream = mocked.open.return_value.__enter__.return_value
                stream.read.return_value = prefix
                self.assertEqual(helper.app_tests_origin_statuses(mocked), (status,))
                mocked.open.assert_called_once_with('rb')
                stream.read.assert_called_once_with(limit + 1)
                mocked.open.return_value.__exit__.side_effect = MemoryError('PRIVATE_SOURCE_CANARY')
                self.assertEqual(helper.app_tests_origin_statuses(mocked), ())
        self.assertEqual(public.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')

    def test_origin_faults_preserve_original_exception_and_cleanup(self):
        marker, status = self.matrix()[0]
        for fault in ('reader', 'unallowlisted', 'write', 'flush'):
            with self.subTest(fault=fault):
                self.assert_origin_run('error: XCTAssertEqual failed\n' + marker,
                                       (status,) if fault == 'flush' else (), fault=fault)

    def test_origin_reader_is_only_called_for_test_assertion_category(self):
        from unittest.mock import patch
        from scripts import private_build as helper
        marker, _ = self.matrix()[0]
        for diagnostic, category in (
            ('', 'unknown'), ('** BUILD FAILED **', 'test-build'),
            ('error: cannot find fixture in scope', 'compile'),
            ('Test execution timed out', 'test-timeout'),
        ):
            with self.subTest(category=category):
                with patch.object(helper, 'app_tests_origin_statuses') as read:
                    self.assert_origin_run(diagnostic + '\n' + marker, category=category)
                read.assert_not_called()

    def test_origin_reader_is_absent_on_success_and_other_stage_failures(self):
        from unittest.mock import MagicMock, patch
        import json
        from scripts import private_build as helper
        for failure in (None, 'swift-package-tests', 'ui-api-readiness', 'non-stage-app-error'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                process = MagicMock()
                process.poll.return_value = 1 if failure == 'ui-api-readiness' else None
                devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-26-1': [
                    {'name': 'iPhone fixture', 'udid': 'fixture-udid'}]}}

                def stage(label, *args):
                    if label == failure:
                        raise helper.StageFailed('PRIVATE_SOURCE_CANARY')
                    if label == 'app-tests' and failure == 'non-stage-app-error':
                        raise OSError('PRIVATE_SOURCE_CANARY')

                with patch.object(helper, 'run_stage', side_effect=stage), \
                        patch.object(helper.subprocess, 'check_output', side_effect=['26.2', json.dumps(devices)]), \
                        patch.object(helper.subprocess, 'Popen', return_value=process), \
                        patch.object(helper.urllib.request, 'urlopen'), \
                        patch.object(helper, 'app_tests_origin_statuses') as read:
                    if failure is None:
                        helper.run_profile('ios', root, root / 'logs')
                    else:
                        error = OSError if failure == 'non-stage-app-error' else helper.StageFailed
                        with self.assertRaises(error):
                            helper.run_profile('ios', root, root / 'logs')
                read.assert_not_called()

    def test_origin_pressure_has_hard_deadline_without_new_subprocesses(self):
        import signal
        from scripts import private_build as helper

        class DeadlineExpired(BaseException):
            pass

        def expire(*_):
            raise DeadlineExpired()

        limit = 1024 * 1024
        matrix = self.matrix()
        valid = '\n'.join(marker for marker, _ in reversed(matrix)) + '\n'
        patterns = (valid, matrix[0][0] + 'suffix\n', 'x', '\n')
        previous = signal.signal(signal.SIGALRM, expire)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / 'app-tests.log'
                for pattern in patterns:
                    with self.subTest(pattern_length=len(pattern)):
                        prefix = 'error: XCTAssertEqual failed\n'
                        diagnostic = prefix + pattern * ((limit - len(prefix)) // len(pattern))
                        expected = tuple(status for _, status in matrix) if pattern == valid else ()
                        log.write_text(diagnostic)
                        self.assertEqual(helper.app_tests_origin_statuses(log), expected)
                        self.assert_origin_run(diagnostic, expected)
        except DeadlineExpired:
            self.fail('Origin protocol stress exceeded 10 seconds')
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    unittest.main()
