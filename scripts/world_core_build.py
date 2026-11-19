"""Run the OpenAstra World Core gate with private stdout/stderr and public status only."""
import os
from pathlib import Path
import re
import subprocess


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
    """Fixed command list. Nothing here comes from the request payload."""
    node = [
        ('npm-install', ['npm', 'ci', '--no-audit', '--no-fund']),
        ('typecheck', ['npm', 'run', 'typecheck']),
        ('unit-suites', ['npm', 'run', 'test']),
        ('contract-gate', ['npm', 'run', 'test:contract']),
        ('migration-guard', ['npm', 'run', 'migrate:check']),
        ('bundle-freshness', ['npm', 'run', 'bundle:check']),
        ('e2e-drill', ['npm', 'run', 'e2e']),
        ('cross-runtime-parity', ['npm', 'run', 'parity']),
        ('asset-hashes', ['npm', 'run', 'assets:verify']),
        ('release-gate', ['npm', 'run', 'release:check']),
    ]
    if profile in ('all', 'node'):
        return node
    raise ValueError('Unknown build profile')


def run_profile(profile, source, logs):
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    for label, args in build_plan(profile):
        run_stage(label, args, source, logs)


if __name__ == '__main__':
    import sys
    try:
        base = Path(os.environ['RUNNER_TEMP'])
        run_profile(sys.argv[1], base / 'world-core-source', base / 'world-core-output')
    except Exception:
        print('Private build failed; raw diagnostics are not published.', file=sys.stderr)
        sys.exit(1)
