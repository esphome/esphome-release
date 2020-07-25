# Notes

This document describes how ESPHome versions are released. **The entire process is automated using the scripts in
this repository**. This document only documents how it works on a high level.

A version always looks like `<major>.<minor>.<patch>[b<beta>]`.

First there are two types of releases:

  - Beta releases: For example `1.15.0b1`, only users on the beta channel get these. Before a minor release there are always one or more beta releases. The `b` indicates a beta release, the numbering after that starts at `1`.
  - Full release: For example `1.15.0` or `1.15.1`, all users on stable get these.

## Prerequisites

Before any of the other steps, it's good to make sure the local repositories are all up to date.

So the `esphomerelease` script goes through all repositories, switches to the main branches and performs a `git pull`.

Additionally, the `current` branch of the docs repo is merged into `next`.

## Release Cutting

There are two different processes for cutting a release, depending on the type of release. First, a release
can be cut by _merging_ from a parent branch, or it can be cut by _cherry picking_ commits in a milestone.

The _first beta release_ for a major/minor version bump (for example `1.14.4` to `1.15.0b1`) is done as follows:

  - Create a new branch `bump-{version}` (for example `bump-1.15.0b1`) from `rc`.
  - Merge `dev` into the new branch.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `rc`.
  - Bump version on `dev` branch to `{major}.{minor+1}.0-dev` and commit.

All other beta releases are created by cherry-picking individual commits.

  - Create a new branch `bump-{version}` (for example `bump-1.15.0b2`) from `rc`.
  - Cherry-pick all PRs included in the milestone `{version}`.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `rc`.

The first stable release:

  - Create a new branch `bump-{version}` (for example `bump-1.15.0`) from `master`.
  - Merge `rc` into the new branch.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `master`.

All other stable releases:

  - Create a new branch `bump-{version}` (for example `bump-1.15.1`) from `master`.
  - Cherry-pick all PRs included in the milestone `{version}`.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `master`.

The same is repeated for `esphome-docs` with `s/dev/next/` and `s/master/current/`

## PR Merging and Releasing

The PR on GitHub can be used to let the CI check if everything's ok. When ready, merge the PR (DO NOT SQUASH, branch protection rules should already disallow this).

Then comes the second step: Tagging the release and publishing it.

  - Set `branch = 'rc' if is_beta_release else 'master'`
  - Create a GitHub release for branch `$branch` tagged `v{version}`, `prerelease=is_beta_release` and publish it.

GitHub Actions will automatically pick up the release, build assets and publish them to registries.

The same is repeated for the docs repo but again with different branch names.

## Home Assistant Add-On release

The Home Assistant Add-On also needs to be updated with the latest version (this is currently done on the maintainer side, in the future this could be automatic with webhooks).

Run `script/bump-version.py [--beta] [--stable]` in the https://github.com/esphome/hassio/ repository.

Push the change, and tag the release `v{version}` in the GitHub releases interface.

## Running `esphomerelease`

Install with `pip3 install -e .` (preferably in a venv)

To create a release, do:

 - `esphomerelease cut-release {version}` - cuts the release and creates PRs in the esphome repos
 - Merge the PRs the step above created.
 - `esphomerelease publish-release {version}` - creates a tag and publishes the release.

For example some releases for 1.15 could look like this:

```bash
$ esphomerelease cut-release 1.15.0b1
# Go to github, merge the PRs (links are printed to console)
$ esphomerelease publish-release 1.15.0b1

# To create release 1.15.0b2:
# Add PRs to the `1.15.0b2` milestone that was automatically created.
# Then run
$ esphomerelease cut-release 1.15.0b2
# Merge release PR
$ esphomerelease publish-release 1.15.0b2

# Now we want to publish 1.15.0
$ esphomerelease cut-release 1.15.0
# Merge release PR
$ esphomerelease publish-release 1.15.0

# Now create a patch release 1.15.1
# Once again add PRs to the milestone 1.15.1, then perform these steps:
$ esphomerelease cut-release 1.15.1
# Merge release PR
$ esphomerelease publish-release 1.15.1
```
