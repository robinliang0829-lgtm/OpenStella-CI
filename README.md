# OpenStella-CI

Public CI orchestration for the private OpenStella application. This repository contains infrastructure only, not the application's source code.

## Current rollout status

The initial capability probe is deliberately credential-free. It supports an owner-triggered `repository_dispatch` (`ci-smoke`), manual dispatch, or a published Release whose tag starts with `ci-smoke-`. It runs on standard Linux and macOS hosted runners without checking out any repository, accessing secrets, uploading artifacts, or using caches.

Do not treat the probe as an application build or a passing OpenStella CI result. Private-source builds must not be enabled before this probe executes successfully and a project-scoped credential is configured.

## Privacy contract for the private-source pipeline

- Keep the application repository private. Never mirror or commit application source into this repository.
- Dispatch payloads may contain only validated, non-sensitive build parameters such as a full commit SHA, version, and an allowlisted build profile. Never include tokens, arbitrary repository URLs, commands, or scripts.
- Restrict privileged triggers to trusted maintainers and verify the requested commit against approved private-source history. A release in the private repository does not automatically trigger an event here; cross-repository dispatch is explicit.
- Do not copy a developer's account-wide CLI OAuth token into Actions secrets. Prefer a GitHub App installed only on the private project, with narrowly scoped, short-lived installation tokens. Source access and dispatch permissions are separate.
- Treat public workflow logs, artifacts, caches, and Release assets as public. Compiler errors, test output, attachments, source maps, and debug symbols can disclose private material. Secret masking does not protect arbitrary source code.
- Capture build and test output without printing private contents to public logs. Private reports and build products require a private destination and a separately scoped publishing credential. Do not upload checkout directories or raw reports here.
- Pin reviewed third-party Actions to full commit SHAs, avoid executing untrusted pull-request code with privileged credentials, and minimize repository write access.

## Billing and policy caveats

A public repository is not a guaranteed remedy for an account billing lock: GitHub documents that a failed payment authorization can also block free-tier Actions access. If the credential-free probe is rejected before execution, stop credential provisioning and resolve the account lock with GitHub.

GitHub's terms require hosted-runner work to relate to production, testing, deployment, or publication of the software project associated with the Actions repository. This repository is dedicated solely to OpenStella's own CI. No claim is made that GitHub has specifically approved a public-CI/private-source billing arrangement; confirm with GitHub Support before relying on it long-term. Larger runners are not part of this standard-runner probe.

References:
- https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features
- https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account
- https://docs.github.com/en/actions/reference/security/secure-use
- https://docs.github.com/en/actions/concepts/security/github_token
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## Local probe-contract check

```sh
python3 -m unittest discover -s tests -v
```
