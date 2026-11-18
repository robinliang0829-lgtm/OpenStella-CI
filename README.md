# OpenStella-CI

Public CI orchestration for the private OpenStella application. This repository contains infrastructure only, not the application's source code.

## Current rollout status

The initial capability probe is deliberately credential-free. It supports an owner-triggered `repository_dispatch` (`ci-smoke`), manual dispatch, or a published Release whose tag starts with `ci-smoke-`. It runs on standard Linux and macOS hosted runners without checking out any repository, accessing secrets, uploading artifacts, or using caches.

Do not treat the probe as an application build or a passing OpenStella CI result. Private-source builds must not be enabled before this probe executes successfully and a project-scoped credential is configured.

## Privacy contract for the private-source pipeline

- Keep the application repository private. Never mirror or commit application source into this repository.
- Dispatch payloads may contain only validated, non-sensitive build parameters such as a full commit SHA, version, and an allowlisted build profile. Never include tokens, arbitrary repository URLs, commands, or scripts.
- Restrict privileged triggers to trusted maintainers and verify the requested commit against approved private-source history. A release in the private repository does not automatically trigger an event here; cross-repository dispatch is explicit.
- Source access uses a dedicated read-only deploy key for OpenStella only (`SOURCE_READ_SSH_KEY`), not a developer's account-wide CLI OAuth token. Its temporary key file is removed before builds run. Source access and dispatch permissions are separate. A project-scoped GitHub App is an alternative for future private publishing.
- Treat public workflow logs, artifacts, caches, and Release assets as public. Compiler errors, test output, attachments, source maps, and debug symbols can disclose private material. Secret masking does not protect arbitrary source code.
- Build/test stdout and stderr are captured in runner-local private files and never echoed to public logs. Public output is stage pass/fail only. Source, logs, derived data and test bundles are removed on completion. This baseline does not retain downloadable reports or build products; debug failures privately, not by making raw logs public. A private report destination requires a separately scoped publishing credential.
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

## Trigger the private-source pipeline

Only the repository owner can trigger privileged builds. Source SHAs must be full lowercase commit hashes in the private application's trusted `main` history. Targets are `all`, `node`, `kit`, and `ios`. The SHA is validated, not normalized; arbitrary URLs, refs, commands, and extra payload fields are rejected.

### repository_dispatch

Run on an authorized maintainer's machine. The local credential stays local; the payload contains only the source SHA and build profile.

```sh
SHA="$(gh api repos/robinliang0829-lgtm/OpenStella/commits/main --jq .sha)"
gh api --method POST repos/robinliang0829-lgtm/OpenStella-CI/dispatches --input - <<JSON
{"event_type":"openstella-ci","client_payload":{"sha":"$SHA","target":"all"}}
JSON
```

### Release

Publish a prerelease in **this CI repository**, tagged `ci-<full-source-sha>`:

```sh
gh release create "ci-$SHA" --repo robinliang0829-lgtm/OpenStella-CI \
  --target main --prerelease --title 'Private source CI request' \
  --notes 'Validation request only; no application source or build outputs are published.'
```

The tag points to CI infrastructure, not application code. Its generated source ZIP contains only the public CI repository. A published release selects `all`; use dispatch to repeat an existing SHA. A release in the private repository does not automatically create a cross-repository event. A billing-blocked private Actions job cannot reliably serve as the dispatch bridge.

Manual workflow dispatch supports the same `sha` and `target` inputs.

## Validation coverage

- Node 22: install, format/lint, simulator API tests, TypeScript, OpenAPI lint, all workspace tests and web build with disposable PostgreSQL 16.
- Linux Swift: OpenStellaKit tests in a pinned Swift image.
- macOS: package tests, XcodeGen, compatible iPhone simulator and app/UI tests without code signing.

There is deliberately no automatic private status callback or binary publishing credential in this baseline. A maintainer can review the public pass/fail result for the requested source SHA; private reporting/publishing can be added separately without exposing source or broad credentials.
