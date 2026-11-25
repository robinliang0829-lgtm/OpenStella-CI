"""Run a fixed private Resident snapshot on OpenStella-CI."""
import json
import os
from pathlib import Path
import sys

from .ci_request import trusted_ci_sha
from .private_build import run_stage
from .private_checkout import checkout

CI_REPOSITORY = 'robinliang0829-lgtm/OpenStella-CI'
CI_OWNER = 'robinliang0829-lgtm'
SOURCE_URL = 'git@github.com:robinliang0829-lgtm/OpenAstra-Resident.git'
KEY_ENV = 'RESIDENT_SOURCE_READ_SSH_KEY'
APPROVED_SHAS = frozenset({'9873b42da759db1d648f6e24858f60b24c88a1ec'})

REFERENCE_EVIDENCE_GUARD = """
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const reference = 'evidence/resident-001/live-migration-reference-v1.json';
const receipt = process.argv[1];
const result = spawnSync(process.execPath, ['dist/src/scripts/personhoodAcceptance.js', reference, receipt], { stdio: 'inherit' });
if (result.error || result.status === null || result.status === 0) process.exit(1);
const input = JSON.parse(fs.readFileSync(reference, 'utf8'));
const audit = JSON.parse(fs.readFileSync(receipt, 'utf8'));
const dimensions = Object.values(audit.dimensions);
if (audit.finalResult !== 'FAIL' || audit.migrationEvidence.runId !== input.migration.runId ||
    !dimensions.length || dimensions.some((item) => item.passed || item.measurementStatus !== 'MISSING_EVIDENCE')) process.exit(1);
"""


def validate_request(event_name, event, actor, repository):
    if event_name != 'workflow_dispatch' or actor != CI_OWNER or repository != CI_REPOSITORY:
        raise ValueError('Untrusted request')
    inputs = event.get('inputs') if isinstance(event, dict) else None
    if not isinstance(inputs, dict) or set(inputs) != {'sha'}:
        raise ValueError('Invalid parameter set')
    sha = inputs['sha']
    if not isinstance(sha, str) or sha not in APPROVED_SHAS:
        raise ValueError('Unapproved source snapshot')
    return sha


def request():
    with open(os.environ['GITHUB_EVENT_PATH']) as event:
        sha = validate_request(os.environ['GITHUB_EVENT_NAME'], json.load(event),
                               os.environ['GITHUB_ACTOR'], os.environ['GITHUB_REPOSITORY'])
    ci_sha = trusted_ci_sha(os.environ['GITHUB_WORKFLOW_SHA'], os.environ['GITHUB_WORKSPACE'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'sha={sha}\nci_sha={ci_sha}\n')


def checkout_resident(sha, key, destination):
    if sha not in APPROVED_SHAS:
        raise ValueError('Unapproved source snapshot')
    checkout(sha, key, destination, source_url=SOURCE_URL,
             approved_feature_shas=APPROVED_SHAS, key_env=KEY_ENV)


def build(source, logs):
    env = dict(os.environ, npm_config_cache=str(logs / 'npm-cache'))
    env.pop(KEY_ENV, None)
    stages = [
        ('dependencies', ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund']),
        ('typescript', ['npm', 'run', 'build']),
        ('cli', ['npm', '--prefix', 'gingoo-cli', 'test']),
        ('resident-and-miniapp', ['npm', 'run', 'test:ci']),
        ('workbench-lease', ['node', 'node_modules/vitest/vitest.mjs', 'run',
                            'tests/workbenchLease.test.ts', 'tests/workbenchProduct.test.ts']),
        ('paired-fixture', ['npm', 'run', 'acceptance:fixture']),
        ('reference-evidence-guard', ['node', '-e', REFERENCE_EVIDENCE_GUARD,
                                     str(logs / 'reference-receipt.json')]),
    ]
    for label, command in stages:
        run_stage(label, command, source, logs, env=env)


def main():
    operation = sys.argv[1]
    if operation == 'request':
        request()
        return
    base = Path(os.environ['RUNNER_TEMP'])
    source = base / 'resident-source'
    if operation == 'checkout':
        checkout_resident(os.environ['REQUESTED_SHA'], os.environ.pop(KEY_ENV, ''), source)
        print('Private source checkout: passed')
    elif operation == 'build':
        build(source, base / 'resident-output')
    else:
        raise ValueError('Unknown operation')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Resident CI step failed; raw diagnostics are withheld.', file=sys.stderr)
        sys.exit(1)
