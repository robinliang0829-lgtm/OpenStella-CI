"""Validate public, non-sensitive build parameters for OpenAstra World Core."""
import re
import subprocess

EVENT_TYPE = 'openastra-world-core-ci'
RELEASE_PREFIX = 'wc-ci-'
TARGETS = frozenset({'all', 'node', 'ope78'})


def trusted_ci_sha(workflow_sha, workspace):
    if not isinstance(workflow_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', workflow_sha):
        raise ValueError('Untrusted CI snapshot')
    result = subprocess.run(['git', '-C', workspace, 'rev-parse', '--verify', 'HEAD'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode or result.stdout.strip() != workflow_sha.encode('ascii'):
        raise ValueError('Untrusted CI snapshot')
    return workflow_sha


def validate_request(event_name, event, actor, owner):
    if not actor or actor != owner or not isinstance(event, dict):
        raise ValueError('Untrusted request')
    if event_name == 'repository_dispatch' and event.get('action') == EVENT_TYPE:
        payload = event.get('client_payload')
    elif event_name == 'workflow_dispatch':
        payload = event.get('inputs')
    elif event_name == 'release' and event.get('action') == 'published':
        release = event.get('release')
        if not isinstance(release, dict) or release.get('draft') is not False:
            raise ValueError('Invalid release')
        tag = release.get('tag_name')
        if not isinstance(tag, str) or not re.fullmatch(RELEASE_PREFIX + r'[0-9a-f]{40}', tag):
            raise ValueError('Invalid release tag')
        payload = {'sha': tag[len(RELEASE_PREFIX):], 'target': 'all'}
    else:
        raise ValueError('Unsupported trigger')
    if not isinstance(payload, dict) or set(payload) != {'sha', 'target'}:
        raise ValueError('Invalid parameter set')
    sha, target = payload['sha'], payload['target']
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid commit SHA')
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError('Invalid build profile')
    return {'sha': sha, 'target': target}


if __name__ == '__main__':
    import json
    import os
    import sys
    try:
        if os.environ.get('GITHUB_REPOSITORY') != 'robinliang0829-lgtm/OpenStella-CI':
            raise ValueError('Wrong CI repository')
        with open(os.environ['GITHUB_EVENT_PATH']) as source:
            event = json.load(source)
        ci_sha = trusted_ci_sha(os.environ.get('GITHUB_WORKFLOW_SHA'), os.environ['GITHUB_WORKSPACE'])
        request = validate_request(os.environ['GITHUB_EVENT_NAME'], event, os.environ.get('GITHUB_ACTOR'), 'robinliang0829-lgtm')
        request['ci_sha'] = ci_sha
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in request.items():
                output.write(f'{key}={value}\n')
    except (ValueError, KeyError, OSError):
        print('Build request rejected; input contents withheld.', file=sys.stderr)
        sys.exit(2)
