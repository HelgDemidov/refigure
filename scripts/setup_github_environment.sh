#!/usr/bin/env bash
# Provision the GitHub-side "pypi" deployment environment used by
# .github/workflows/publish.yml's trusted-publishing (OIDC) job.
#
# This is the repository-side half of PyPI trusted publishing —
# reversible, requires no third-party (PyPI) login, and is exactly what
# `environment: pypi` in publish.yml resolves against. The OTHER half (registering
# HelgDemidov/refigure + publish.yml + this environment name as a trusted
# publisher ON pypi.org) is NOT done here — that needs the PyPI project
# owner's own login and stays a manual step, see the spec's §4.
#
# What this does, idempotently (safe to re-run — every step either updates
# in place or is skipped if already correct):
#   1. PUT   /repos/{owner}/{repo}/environments/{env}
#            creates (or updates) the "pypi" environment with a CUSTOM
#            deployment branch/tag policy (deployment_branch_policy.
#            custom_branch_policies=true, protected_branches=false — the
#            two are mutually exclusive per GitHub's own docs, confirmed
#            live 2026-08-07 against
#            https://docs.github.com/en/rest/deployments/environments).
#   2. GET   /repos/{owner}/{repo}/environments/{env}/deployment-branch-policies
#            checked FIRST — the POST below is not idempotent on GitHub's
#            side (calling it twice with the same name+type is rejected,
#            not silently deduplicated), so this script has to do the
#            dedup itself to be safely re-runnable.
#   3. POST  /repos/{owner}/{repo}/environments/{env}/deployment-branch-policies
#            body {"name":"v*","type":"tag"} — ONLY if step 2 didn't
#            already find a "v*"/tag policy. This is what actually
#            restricts the "pypi" environment to tag pushes matching
#            v* (v0.1.0, v1.2.3, ...), matching publish.yml's own
#            `on: push: tags: ["v*"]` trigger.
#
# Scope is deliberately narrow: this script touches ONLY the "pypi"
# environment's deployment policy. It does not touch branch protection,
# required_status_checks, enforce_admins, or any other repo Settings —
# those are configured elsewhere (see CLAUDE.md's Git workflow section)
# and a release-automation script has no business changing them.
#
# NOT executed as part of landing this script — provisioning a real repo's
# Settings is a deliberate, one-time action the human orchestrator runs,
# not something a coding agent should trigger as a side effect of writing
# the script. Run it yourself when ready:
#   ./scripts/setup_github_environment.sh
#
# Requires: `gh` CLI, authenticated (`gh auth status`) with a token that
# has the `repo` scope on HelgDemidov/refigure — GitHub's own docs state
# "OAuth app tokens and personal access tokens (classic) need the repo
# scope to use this endpoint" for the environments API.
set -euo pipefail

OWNER="HelgDemidov"
REPO="refigure"
ENV_NAME="pypi"
TAG_PATTERN="v*"
API_VERSION="2022-11-28"

log() {
    printf '%s\n' "$*" >&2
}

die() {
    log "error: $*"
    exit 1
}

# --- preflight -------------------------------------------------------------

command -v gh >/dev/null 2>&1 || die "GitHub CLI ('gh') not found on PATH — install it first: https://cli.github.com/"

gh auth status >/dev/null 2>&1 || die "'gh' is not authenticated — run 'gh auth login' first"

# Sanity check only (does not push/mutate anything): if run from inside a
# git checkout of this repo, confirm 'origin' actually points at
# HelgDemidov/refigure before mutating THAT repo's Settings via the API.
# Skipped, with a warning, if not run from inside a git working tree (e.g.
# a bare checkout, or invoked from elsewhere) — this is a courtesy check
# against fat-fingering the wrong repo, not a hard dependency.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    origin_url="$(git remote get-url origin 2>/dev/null || true)"
    if [ -z "$origin_url" ]; then
        log "warning: no 'origin' remote found — skipping origin sanity check"
    elif ! printf '%s' "$origin_url" | grep -qiE "(github\.com[:/])${OWNER}/${REPO}(\.git)?/?$"; then
        die "'origin' remote ('$origin_url') does not look like https://github.com/${OWNER}/${REPO} — refusing to provision a possibly-wrong repo's environment. Pass different OWNER/REPO in this script if that's intentional."
    fi
else
    log "warning: not inside a git working tree — skipping origin sanity check"
fi

# Explicit confirmation before mutating live repo Settings — this script IS
# meant to be run for real by a human orchestrator, but not silently or
# accidentally (e.g. by a wrapper script piping input to it). Set
# SETUP_GITHUB_ENVIRONMENT_YES=1 to skip the prompt in a non-interactive
# context where the operator has already confirmed intent out-of-band.
if [ "${SETUP_GITHUB_ENVIRONMENT_YES:-0}" != "1" ]; then
    printf 'About to provision the "%s" deployment environment on %s/%s (tag policy: %s).\nContinue? [y/N] ' \
        "$ENV_NAME" "$OWNER" "$REPO" "$TAG_PATTERN" >&2
    read -r reply
    case "$reply" in
        y | Y | yes | YES) ;;
        *) die "aborted — nothing was changed" ;;
    esac
fi

# --- 1. create/update the environment with a custom branch/tag policy -----

log "PUT /repos/${OWNER}/${REPO}/environments/${ENV_NAME} (custom_branch_policies=true)"
gh api \
    --method PUT \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: ${API_VERSION}" \
    "repos/${OWNER}/${REPO}/environments/${ENV_NAME}" \
    --input - >/dev/null <<'JSON'
{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON

# --- 2. check for an existing v*/tag policy before creating one -----------

log "GET /repos/${OWNER}/${REPO}/environments/${ENV_NAME}/deployment-branch-policies (idempotency check)"
existing_id="$(
    gh api \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        "repos/${OWNER}/${REPO}/environments/${ENV_NAME}/deployment-branch-policies" \
        --jq ".branch_policies[] | select(.name == \"${TAG_PATTERN}\" and .type == \"tag\") | .id" \
        2>/dev/null || true
)"

if [ -n "$existing_id" ]; then
    log "tag policy '${TAG_PATTERN}' already present (id ${existing_id}) — nothing to do"
    exit 0
fi

# --- 3. create the v* tag policy -------------------------------------------

log "POST /repos/${OWNER}/${REPO}/environments/${ENV_NAME}/deployment-branch-policies (name=${TAG_PATTERN}, type=tag)"
gh api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: ${API_VERSION}" \
    "repos/${OWNER}/${REPO}/environments/${ENV_NAME}/deployment-branch-policies" \
    --input - >/dev/null <<JSON
{"name":"${TAG_PATTERN}","type":"tag"}
JSON

log "done — '${ENV_NAME}' environment is restricted to tag pushes matching '${TAG_PATTERN}'"
