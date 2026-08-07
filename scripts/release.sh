#!/usr/bin/env bash
# Release runner (docs/release/release-gate/release-gate-2026-08-06.md §3a).
#
# Usage:
#   ./scripts/release.sh 0.1.0
#
# Does, in order:
#   1. Validate the version argument is (roughly) semver-shaped.
#   2. Update pyproject.toml's `version = "..."` field to the new version.
#   3. Commit that one-line change.
#   4. Create a LOCAL git tag `vX.Y.Z` for the new commit.
#   5. Print the exact `git push origin vX.Y.Z` command as an instruction.
#
# Deliberately does NOT run `git push`, ever, for anything — pushing the
# tag is what actually triggers .github/workflows/publish.yml and
# publishes to PyPI, which is IRREVERSIBLE (PyPI does not allow reusing a
# version number even after a `yank`). That has to stay a separate,
# explicit, consciously-taken action by whoever is running this script,
# not a side effect buried at the end of a version-bump script. The local
# tag this script DOES create is fully reversible on its own
# (`git tag -d vX.Y.Z`) right up until it's pushed.
#
# Fails closed: every check below runs BEFORE any mutation (no partial
# version bump, no orphaned commit, no tag left behind on bad input).
set -euo pipefail

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# --- argument validation ----------------------------------------------------

if [ "$#" -ne 1 ]; then
    die "usage: $0 X.Y.Z   (e.g. $0 0.1.0)"
fi

version="$1"

# "Roughly semver-shaped" (spec §3a's own phrasing) — MAJOR.MINOR.PATCH
# required, an optional -prerelease and/or +build-metadata suffix allowed,
# not a full semver.org-grammar regex. Good enough to reject obvious
# mistakes (a stray "v" prefix, "1.0", "latest") without this script
# becoming the source of truth for what a valid version looks like.
semver_re='^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$'
if ! printf '%s' "$version" | grep -qE "$semver_re"; then
    die "'$version' doesn't look like a semver MAJOR.MINOR.PATCH (e.g. 0.1.0) — got: $version"
fi

tag="v${version}"

# --- preflight (all read-only; nothing below mutates anything yet) --------

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
cd "$repo_root"

pyproject="$repo_root/pyproject.toml"
[ -f "$pyproject" ] || die "$pyproject not found"

grep -qE '^version = "[^"]*"$' "$pyproject" || die "no \`version = \"...\"\` line found in $pyproject — refusing to guess where to edit"

# A dirty working tree would get the version-bump commit mixed up with
# unrelated in-progress changes — require a clean tree first.
if [ -n "$(git status --porcelain)" ]; then
    die "working tree is not clean (git status --porcelain) — commit or stash your changes first"
fi

# Tags are effectively immutable once pushed (PyPI won't accept a re-used
# version number) — refuse to silently reuse/overwrite a local tag either.
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    die "tag '$tag' already exists locally — refusing to overwrite it (git tag -d $tag first if that's really intended)"
fi

current_version="$(grep -E '^version = "[^"]*"$' "$pyproject" | sed -E 's/^version = "([^"]*)"$/\1/')"
if [ "$current_version" = "$version" ]; then
    die "pyproject.toml is already at version $version — nothing to bump"
fi

# --- mutate: bump version, commit, tag (local only) ------------------------

echo "bumping version: $current_version -> $version"
sed -i.bak -E "s/^version = \"[^\"]*\"\$/version = \"$version\"/" "$pyproject"
rm -f "$pyproject.bak"

git add "$pyproject"
git commit -m "chore: release $version"

git tag -a "$tag" -m "$version"

echo
echo "Done — local commit + tag created, nothing pushed."
echo "Next step (manual, deliberate — this is what actually publishes to PyPI):"
echo
echo "    git push origin $tag"
echo
