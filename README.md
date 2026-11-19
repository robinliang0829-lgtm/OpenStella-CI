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

Only the repository owner can trigger privileged builds. Source SHAs must be full lowercase commit hashes in the private application's trusted `main` history **or exact entries in the trusted CI configuration's static `APPROVED_FEATURE_SHAS` set**. That set is empty by default. Targets are `all`, `node`, `kit`, and `ios`. The SHA is validated, not normalized; arbitrary URLs, refs, commands, approval lists, and extra payload fields are rejected. Request parameters remain exactly `sha` and `target`.

### Approving an unmerged snapshot

1. Review the final private commit and its full diff, including build scripts, dependency changes, and tests. Approval authorizes code execution, not merge. A baseline SHA or a branch name is not approval of the final UI work.
2. In a separate reviewed public CI change, add only that exact full SHA to `scripts/private_checkout.py::APPROVED_FEATURE_SHAS` on CI `main`. Do not publish private diffs, code, or branch descriptions. Never load approvals from payloads, environment variables, URLs, or private source files. The local contract test must assert the explicitly reviewed approval set and be updated deliberately alongside each approval or revocation.
3. Dispatch the approved SHA with an existing target. The helper fetches it only from the fixed OpenStella repository, requires a commit object, and verifies final `HEAD` equals the requested SHA. A fetch or integrity failure rejects the build; there is no arbitrary-ref fallback. Parents, children, and other branch commits do not inherit approval (commits already in trusted `main` remain allowed independently).
4. A changed feature SHA requires fresh review and approval. Remove temporary entries after acceptance or revocation. The currently approved snapshot is `886ff76ad754acd58bd30aed65ce5a63c3c290cf`; this is execution approval only, not merge approval.

### Trusted CI snapshot and revocation limits

The request job explicitly checks out CI `main`. Before emitting any request outputs, `ci_request.py` requires the running workflow's `GITHUB_WORKFLOW_SHA` to equal that checkout's `HEAD`. It emits the verified infrastructure `ci_sha` internally, and every build job checks out that same immutable CI SHA. `ci_sha` is not a request input. If CI `main` advances between event creation and checkout, the mismatch fails closed: issue a new request using the current trusted workflow rather than bypassing the check. Release tags must likewise refer to the current trusted CI snapshot when the request checkout occurs.

Removing an approval affects requests validated against the updated configuration; it does not retroactively stop jobs already validated or running. Re-running an older workflow version is not guaranteed to enforce the new guard or updated revocations. Cancel affected pending/running jobs and restrict old-version reruns operationally; disable source access if urgent containment is required. Older CI versions may not contain these protections at all.

The CI repository's administrators and reviewed CI configuration remain trusted. These checks are not a sandbox against a malicious CI administrator, a modified workflow that removes them, or arbitrary old-version reruns. Read-only source access limits repository writes, and redirecting stdout/stderr prevents ordinary diagnostic publication; neither prevents malicious build/test/dependency code from using the network to exfiltrate source. Only reviewed code should be approved. No new credential scope, public report/artifact upload, or cache is introduced.

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

## OpenAstra World Core pipeline (second project)

`openastra-world-core` (private, Apache-2.0) runs its gate on this repository's free
runners for the same reason: the source account's Actions are billing-locked. It is a
separate workflow (`world-core-ci.yml`), a separate event type, a separate read-only
deploy key (`WORLD_CORE_SOURCE_READ_SSH_KEY`, scoped to `openastra-world-core` only) and
a separate parameter set — the OpenStella pipeline above is untouched.

- Event: `openastra-world-core-ci`; payload is exactly `{sha, target}` with
  `target ∈ {all, node}`; `sha` must be a full lowercase 40-hex commit inside a reviewed
  ref of the source repo: `refs/heads/main` or `refs/heads/integ/wc` (the integration
  branch under review; drop it from `TRUSTED_REFS` after the work lands on `main`).
- Release path: a published release **in this repository** tagged `wc-ci-<full-sha>`
  selects `target=all`. A release in the private repository does not dispatch anything.
- Builds run `npm ci` and then the repository's own gate: typecheck, four layer suites,
  contract conformance, migration + bundle freshness, e2e drill, cross-runtime parity,
  asset hashes, and the release gate (which packs and re-imports the tarball).
- Same privacy contract as above: public output is stage pass/fail only; stdout/stderr
  stay in runner-local private files; the source, logs and `dist/` are deleted afterwards;
  no artifacts, caches or releases are uploaded. Godot is not installed on the runner, so
  the Godot-specific contract replay reports a skip (it is not a failure).

```sh
gh api --method POST repos/robinliang0829-lgtm/OpenStella-CI/dispatches --input - <<JSON
{"event_type":"openastra-world-core-ci","client_payload":{"sha":"<40-hex>","target":"all"}}
JSON
```
