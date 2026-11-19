"""Fetch one approved private snapshot, without putting credentials in Git config."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

# Only maintainers reviewing this trusted CI configuration may approve snapshots.
# Never populate this set from a request, environment, or private source checkout.
APPROVED_FEATURE_SHAS = frozenset()
SOURCE_URL = 'git@github.com:robinliang0829-lgtm/OpenStella.git'
HOST_KEY = 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n'


def checkout(sha, key_material, destination):
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid commit SHA')
    if not key_material or destination.exists():
        raise ValueError('Invalid checkout configuration')
    destination.mkdir(mode=0o700, parents=True)
    with tempfile.TemporaryDirectory(prefix='.source-key-', dir=destination.parent) as temporary:
        key = Path(temporary) / 'key'
        # OpenSSH requires a final LF; gh secret set trims CR/LF from stdin.
        key.write_text(key_material if key_material.endswith("\n") else key_material + "\n")
        key.chmod(0o600)
        hosts = Path(temporary) / 'known_hosts'
        hosts.write_text(HOST_KEY)
        env = dict(os.environ)
        env.pop('SOURCE_READ_SSH_KEY', None)
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['GIT_SSH_COMMAND'] = f'ssh -F /dev/null -i {shlex.quote(str(key))} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 -o UserKnownHostsFile={shlex.quote(str(hosts))}'
        def git(*args, required=True):
            result = subprocess.run(['git', '-C', str(destination), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if required and result.returncode:
                raise ValueError('Private git operation failed')
            return result
        git('init', '--quiet')
        git('config', '--local', 'core.hooksPath', '/dev/null')
        git('remote', 'add', 'origin', SOURCE_URL)
        if sha in APPROVED_FEATURE_SHAS:
            git('fetch', '--quiet', '--filter=blob:none', '--no-tags', 'origin', sha)
        else:
            git('fetch', '--quiet', '--filter=blob:none', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main')
            if git('merge-base', '--is-ancestor', sha, 'refs/remotes/origin/main', required=False).returncode:
                raise ValueError('Requested commit is outside trusted main history')
        if git('cat-file', '-t', sha).stdout.strip() != b'commit':
            raise ValueError('Requested object is not a commit')
        git('-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', sha)
        if git('rev-parse', '--verify', 'HEAD').stdout.strip() != sha.encode('ascii'):
            raise ValueError('Private checkout integrity check failed')


if __name__ == '__main__':
    import sys
    try:
        checkout(os.environ.get('REQUESTED_SHA'), os.environ.pop('SOURCE_READ_SSH_KEY', ''), Path(os.environ['RUNNER_TEMP']) / 'private-source')
        print('Approved private source snapshot prepared; source and credentials withheld.')
    except (ValueError, OSError, KeyError):
        print('Private checkout rejected or failed; details withheld.', file=sys.stderr)
        sys.exit(2)
