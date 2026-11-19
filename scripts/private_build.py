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
            run_stage('app-tests', ['xcodebuild', 'test', '-project', 'ios/OpenStella.xcodeproj', '-scheme', 'OpenStella', '-destination', f'id={udid}', '-parallel-testing-enabled', 'NO', '-derivedDataPath', str(logs / 'derived'), '-resultBundlePath', str(logs / 'app.xcresult'), 'OPENSTELLA_API_BASE_URL=http://127.0.0.1:18788', 'OPENSTELLA_ALLOWS_ARBITRARY_LOADS=YES', 'CODE_SIGNING_ALLOWED=NO'], source, logs)
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
