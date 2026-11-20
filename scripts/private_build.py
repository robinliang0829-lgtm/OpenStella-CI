"""Run trusted build commands with private stdout/stderr and public status only."""
import os
from pathlib import Path
import re
import subprocess
import json
import time
import urllib.request


class StageFailed(RuntimeError):
    pass


def run_stage(label, args, source, logs, env=None):
    if not re.fullmatch(r'[a-z0-9-]+', label):
        raise StageFailed('Invalid stage')
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (logs / f'{label}.log').open('wb') as output:
        result = subprocess.run(args, cwd=source, env=env, stdout=output, stderr=subprocess.STDOUT)
    print(f'{label}: {"passed" if result.returncode == 0 else "failed"}', flush=True)
    if result.returncode:
        raise StageFailed(label)

def classify_app_tests_failure(log, *, build_reason=False, type_inference_detail=False, assertion_detail=False):
    """Return a fixed evidence category; never return private diagnostics."""
    try:
        # Bound allocation even if the file grows; never classify a partial log.
        limit = 1024 * 1024
        with log.open('rb') as output:
            data = output.read(limit + 1)
        if len(data) > limit:
            return 'unknown'
        text = data.decode('utf-8')
    except Exception:
        return 'unknown'
    # Each line gets only a fixed number of forward scans: never retry a
    # greedy suffix at every diagnostic prefix on adversarial input.
    lines = text.split('\n')
    for line in lines:
        assertion = re.search(r'\b(?:XCTAssert\w*|XCTUnwrap)\b', line)
        body = line.partition('error: -[')[2].partition('] : ')[2]
        if ((assertion and re.search(r'\bfailed\b', line[assertion.end():]))
                or re.match(r'failed\b', body)):
            if assertion_detail:
                location = ''
                if line.startswith('error: '):
                    message = line[7:]
                else:
                    location, separator, message = line.partition(': error: ')
                    if not separator or not re.fullmatch(r'/[A-Za-z0-9_./ -]+\.swift:[0-9]+(?::[0-9]+)?', location):
                        return 'unknown'
                if message.startswith('-['):
                    header, separator, message = message.partition('] : ')
                    if not separator or not re.fullmatch(r'-\[[A-Za-z0-9_.]+ [A-Za-z0-9_]+', header):
                        return 'unknown'
                parts = location.partition(':')[0].split('/')[1:-1]
                groups = {'ui-tests' if part.endswith('UITests') else 'hosted-tests'
                          for part in parts if part.endswith('Tests')}
                target = groups.pop() if len(groups) == 1 and not any(part in ('.', '..') for part in parts) else 'unknown'
                for kind, prefix in (('true', 'XCTAssertTrue failed - '),
                                     ('false', 'XCTAssertFalse failed - '),
                                     ('equal', 'XCTAssertEqual failed: '),
                                     ('unwrap', 'XCTUnwrap failed: ')):
                    if message.startswith(prefix):
                        # Only exact, type-qualified failed operands; never quoted suffix text.
                        if kind == 'equal' and message.partition(' - ')[0] in (
                            'XCTAssertEqual failed: ("XCTWaiterResult(rawValue: 2)") is not equal to ("XCTWaiterResult(rawValue: 1)")',
                            'XCTAssertEqual failed: ("XCTWaiter.Result(rawValue: 2)") is not equal to ("XCTWaiter.Result(rawValue: 1)")',
                        ):
                            kind = 'wait-timeout'
                        return f'assertion-{kind}-target-{target}'
                return 'unknown'
            return 'test-assertion'
    # XCTest assertion bodies above are not independent timeout evidence.
    for line in lines:
        body = line.partition('] : ')[2] if line.startswith('error: -[') else line
        if (re.match(r'Test execution timed out\b', body)
                or body.startswith(('Exceeded execution time allowance of ',
                                    'Asynchronous wait failed: Exceeded timeout of '))):
            return 'test-timeout'
    if re.search(r'(?:Undefined symbols for architecture |linker command failed)', text):
        return 'link'
    if re.search(r'(?:Unable to find a destination matching the provided destination specifier|Ineligible destinations for the )', text):
        return 'destination'
    if re.search(r'Failed to (?:install|launch) the app', text):
        return 'install-launch'
    if any(any(marker in line.partition('error:')[2] for marker in (
            'has no member ', 'is only available in iOS ', 'is unavailable in iOS'))
           for line in lines):
        return 'compile-member-api'
    if ('error: cannot convert value of type ' in text
            or any(line.partition('error: cannot find ')[2].find(' in scope') > 0
                   for line in lines)):
        return 'compile'
    if re.search(r'(?:CoreSimulatorService connection became invalid|(?:Failed|Unable) to boot (?:the )?[Ss]imulator|Failed to establish communication with the test runner)', text):
        return 'simulator-startup-communication'
    if any(marker in text for marker in ('** TEST BUILD FAILED **', '** BUILD FAILED **', 'Testing cancelled because the build failed.')):
        if build_reason or type_inference_detail:
            for line in lines:
                location = ''
                if line.startswith('error: '):
                    message = line[7:]
                else:
                    location, separator, message = line.partition(': error: ')
                    # Conservative absolute Swift locations only; reject quoted/referenced text.
                    if not separator or not re.fullmatch(r'/[A-Za-z0-9_./ -]+\.swift:[0-9]+:[0-9]+', location):
                        continue
                # Fixed starts/required phrases only; no identifiers leave here.
                for reason, prefixes, required in (
                    ('build-actor-isolation', ('call to main actor-isolated ', 'main actor-isolated ', 'actor-isolated '), ()),
                    ('build-argument-shape', ('missing argument for parameter ', 'missing arguments for parameters ', 'extra argument ', 'extra arguments at positions ', 'incorrect argument label', 'no exact matches in call to '), ()),
                    ('build-try-await', ("call can throw but is not marked with 'try'", "expression is 'async' but is not marked with 'await'", "call is 'async' but is not marked with 'await'"), ()),
                    ('build-override-conformance', ('type ',), (' does not conform to protocol ',)),
                    ('build-override-conformance', ('method does not override any method from its superclass', 'property does not override any property from its superclass'), ()),
                    ('build-type-inference', ('generic parameter ',), (' could not be inferred',)),
                    ('build-type-inference', ('the compiler is unable to type-check this expression in reasonable time', 'unable to type-check this expression in reasonable time', 'ambiguous use of '), ()),
                    ('build-syntax-parser', ('expected expression', 'expected declaration', "expected '}'"), ()),
                    ('build-module-import', ('no such module ', 'missing required module ', 'failed to build module ', 'could not build module '), ()),
                    ('build-compiler-internal', ('failed to produce diagnostic for expression', 'compiler crashed'), ()),
                    ('build-resource-limit', ('out of memory', 'cannot allocate memory', 'unable to make temporary file: No space left on device'), ()),
                ):
                    if message.startswith(prefixes) and all(marker in message for marker in required):
                        if type_inference_detail and reason == 'build-type-inference':
                            if message.startswith(('the compiler is unable to type-check this expression in reasonable time',
                                                   'unable to type-check this expression in reasonable time')):
                                kind = 'typecheck-complexity'
                            elif message.startswith('ambiguous use of '):
                                kind = 'ambiguous-use'
                            else:
                                kind = 'generic-inference'
                            # Use only this error's directory parts, never filenames or summaries.
                            parts = location.rsplit(':', 2)[0].split('/')[1:-1]
                            target = 'unknown'
                            if parts and not any(part in ('.', '..') for part in parts):
                                if any(left == 'ios' and right == 'OpenStella'
                                       for left, right in zip(parts, parts[1:])):
                                    target = 'app'
                                if any(part.endswith('UITests') for part in parts):
                                    target = 'ui-tests'
                                elif any(part.endswith('Tests') for part in parts):
                                    target = 'hosted-tests'
                            return f'build-{kind}-target-{target}'
                        return reason
        return 'test-build'
    return 'unknown'


# Only these complete protocol lines and public statuses may cross the boundary.
# Checkpoints describe fixed semantics, not testcase identities or source paths.
# The trusted callback guarantees truth-only V1 emission; this reader does not
# infer provenance from neighboring records. Publication requires root test-assertion.
APP_TESTS_ORIGIN_STATUSES = {
    **{f'OPE12_ORIGIN_V1 failure={failure} area={area}':
       f'app-tests-origin-{failure}-area-{area}: failed'
       for failure in ('truth', 'equality', 'wait-timeout', 'unwrap', 'other')
       for area in ('bootstrap', 'onboarding', 'layout', 'navigation', 'image-check', 'other')},
    'OPE12_CHECKPOINT_V1 checkpoint=other': 'app-tests-checkpoint-other: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=element-existence': 'app-tests-checkpoint-element-existence: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=accessibility-hit': 'app-tests-checkpoint-accessibility-hit: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=selected-state': 'app-tests-checkpoint-selected-state: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=frame-containment': 'app-tests-checkpoint-frame-containment: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=scroll-reachability': 'app-tests-checkpoint-scroll-reachability: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=content-value': 'app-tests-checkpoint-content-value: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=async-progress': 'app-tests-checkpoint-async-progress: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=request-isolation': 'app-tests-checkpoint-request-isolation: failed',
    'OPE12_CHECKPOINT_V1 checkpoint=scene-identity': 'app-tests-checkpoint-scene-identity: failed',
}


def app_tests_origin_statuses(log):
    """Read bounded, exact protocol lines; never infer from diagnostic prose."""
    try:
        limit = 1024 * 1024
        with log.open('rb') as output:
            data = output.read(limit + 1)
        if len(data) > limit:
            return ()
        # Only LF/CRLF delimit lines; preserve all other characters for exact matching.
        found = {line for line in data.decode('utf-8').replace('\r\n', '\n').split('\n')
                 if line in APP_TESTS_ORIGIN_STATUSES}
        return tuple(status for line, status in APP_TESTS_ORIGIN_STATUSES.items()
                     if line in found)
    except Exception:
        return ()


def build_plan(profile):
    swift = [('swift-package-tests', ['swift', 'test', '--package-path', 'ios/Packages/OpenStellaKit'])]
    if profile == 'kit':
        return swift
    if profile == 'node':
        return [('npm-install', ['npm', 'ci']), ('format-lint', ['npx', 'biome', 'ci', '.']), ('simulator-api-tests', ['node', '--test', 'ios/UITestServer/server.test.mjs']), ('typecheck', ['npm', 'run', 'typecheck']), ('contract-lint', ['npm', 'run', 'lint', '-w', 'contracts']), ('workspace-tests', ['npm', 'test']), ('web-build', ['npm', 'run', 'build'])]
    if profile == 'ios':
        return swift + [('install-xcodegen', ['brew', 'install', 'xcodegen']), ('xcode-project', ['xcodegen', 'generate'])]
    raise ValueError('Unknown build profile')


def choose_simulator(sdk, devices):
    version = lambda text: tuple(int(part) for part in re.findall(r'\d+', text))
    sdk_version = version(sdk)
    candidates = []
    for runtime, entries in devices.items():
        match = re.search(r'SimRuntime\.iOS-([\d-]+)$', runtime)
        if not match or version(match.group(1)) < (17, 0):
            continue
        for device in entries:
            if device.get('isAvailable', True) and device.get('name', '').startswith('iPhone'):
                candidates.append((version(match.group(1)), device['name'], device['udid']))
    supported = [entry for entry in candidates if entry[0] <= sdk_version]
    fallback = [entry for entry in candidates if entry[0] > sdk_version and entry[0][0] == sdk_version[0]]
    if supported:
        selected = max(entry[0] for entry in supported)
        pool = [entry for entry in supported if entry[0] == selected]
    elif fallback:
        selected = min(entry[0] for entry in fallback)
        pool = [entry for entry in fallback if entry[0] == selected]
    else:
        raise ValueError('No compatible simulator')
    return min(pool, key=lambda entry: (len(entry[1]), entry[1]))[2]

def run_profile(profile, source, logs):
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    for label, args in build_plan(profile):
        run_stage(label, args, source / 'ios' if label == 'xcode-project' else source, logs)
    if profile != 'ios':
        return
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], text=True, stderr=subprocess.DEVNULL)
    devices = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'devices', 'available', '-j'], text=True, stderr=subprocess.DEVNULL))['devices']
    udid = choose_simulator(sdk, devices)
    with (logs / 'ui-api.log').open('wb') as output:
        process = subprocess.Popen(['node', 'ios/UITestServer/server.mjs'], cwd=source, env={**os.environ, 'PORT': '18788'}, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise StageFailed('ui-api-readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18788/v0/health', timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            try:
                run_stage('app-tests', ['xcodebuild', 'test', '-project', 'ios/OpenStella.xcodeproj', '-scheme', 'OpenStella', '-destination', f'id={udid}', '-parallel-testing-enabled', 'NO', '-derivedDataPath', str(logs / 'derived'), '-resultBundlePath', str(logs / 'app.xcresult'), 'OPENSTELLA_API_BASE_URL=http://127.0.0.1:18788', 'OPENSTELLA_ALLOWS_ARBITRARY_LOADS=YES', 'CODE_SIGNING_ALLOWED=NO'], source, logs)
            except StageFailed:
                # Diagnostics are best-effort; never replace the business failure.
                try:
                    category = classify_app_tests_failure(logs / 'app-tests.log')
                except Exception:
                    category = 'unknown'
                try:
                    print(f'app-tests-{category}: failed', flush=True)
                    if category == 'test-assertion':
                        detail = classify_app_tests_failure(logs / 'app-tests.log', assertion_detail=True)
                        if detail in {f'assertion-{kind}-target-{target}'
                                      for kind in ('true', 'false', 'equal', 'unwrap', 'wait-timeout')
                                      for target in ('hosted-tests', 'ui-tests', 'unknown')}:
                            print(f'app-tests-{detail}: failed', flush=True)
                        for status in app_tests_origin_statuses(logs / 'app-tests.log'):
                            if status in APP_TESTS_ORIGIN_STATUSES.values():
                                print(status, flush=True)
                    if category == 'test-build':
                        reason = classify_app_tests_failure(logs / 'app-tests.log', build_reason=True)
                        if reason.startswith('build-'):
                            print(f'app-tests-{reason}: failed', flush=True)
                            if reason == 'build-type-inference':
                                detail = classify_app_tests_failure(logs / 'app-tests.log', type_inference_detail=True)
                                if detail in {f'build-{kind}-target-{target}'
                                              for kind in ('typecheck-complexity', 'ambiguous-use', 'generic-inference')
                                              for target in ('app', 'hosted-tests', 'ui-tests', 'unknown')}:
                                    print(f'app-tests-{detail}: failed', flush=True)
                except Exception:
                    pass
                raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    import sys
    try:
        base = Path(os.environ['RUNNER_TEMP'])
        run_profile(sys.argv[1], base / 'private-source', base / 'private-output')
    except Exception:
        print('Private build failed; raw diagnostics are not published.', file=sys.stderr)
        sys.exit(1)
