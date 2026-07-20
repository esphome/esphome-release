# Notes

This document describes how ESPHome versions are released. **The entire process is automated using the scripts in
this repository**. This document only documents how it works on a high level.

ESPHome uses calendar versioning. A version always looks like `<year>.<month>.<patch>[b<beta>]` (for example `2026.6.0`), where `month` is the release month and `patch` starts at `0`.

First there are two types of releases:

  - Beta releases: For example `2026.6.0b1`, only users on the beta channel get these. Before each new monthly release there are always one or more beta releases. The `b` indicates a beta release, the numbering after that starts at `1`.
  - Full release: For example `2026.6.0` or `2026.6.1`, all users on stable get these.

## Milestones

A whole release cycle shares a single milestone named after the final release (for example `2026.6.0`). It is opened when the previous monthly `.0` release goes out (so cutting `2026.5.0` opens the `2026.6.0` milestone), which lets PRs be marked for it during the dev cycle to flag that they should be prioritized for review and merge before the first beta is cut. PRs destined for the cycle stay assigned to it through the dev and beta period, and the milestone stays open until its own final release closes it. Patch releases (`2026.6.1`, `2026.6.2`, …) keep their own per-version milestone. When the first beta of the next month is cut, any leftover open patch milestones from the previous month (e.g. an unused `2026.6.3`) are closed.

The cycle milestone's due date tracks the schedule: when it is opened the due date is the new-component/feature merge deadline (the Monday before the second Wednesday of its month — new components and big features should be merged by then to make the release; after it they generally wait for the next one, while bug fixes still go in), and when the first beta is cut the due date is moved to release day (the third Wednesday of the month).

The first beta is cut by merging `dev`, which brings in every PR already merged for the cycle; those merged PRs are then removed from the milestone so later beta cuts don't cherry-pick them again (open PRs keep their milestone). At each later beta cut, every PR remaining in the cycle milestone that is merged and not already labelled `cherry-picked` is cherry-picked, then labelled. The final release does the same for any stragglers — merged milestone PRs that never made it into a beta — so take care not to leave anything in the milestone that should not ship.

Open PRs on the milestone are always reported when cutting. For betas this is only a warning and cutting continues; for full releases it blocks until the milestone is clear (or you abort).

## Prerequisites

Before any of the other steps, it's good to make sure the local repositories are all up to date.

So the `esphomerelease` script goes through all repositories, switches to the main branches and performs a `git pull`.

Additionally, the `current` branch of the docs repo is merged into `next` and `beta`. Once the cut has finished successfully, those two docs branches are pushed so the merge lands on the remote.

## Release Cutting

There are two different processes for cutting a release, depending on the type of release. First, a release
can be cut by _merging_ from a parent branch, or it can be cut by _cherry picking_ commits in a milestone.

The _first beta release_ for a new monthly version (for example `2026.5.4` to `2026.6.0b1`) is done as follows:

  - Create a new branch `bump-{version}` (for example `bump-2026.6.0b1`) from `rc`.
  - Merge `dev` into the new branch.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `rc`.
  - Bump version on `dev` branch to the next month's dev version `{year}.{month+1}.0-dev` (for example `2026.7.0-dev`) and commit.

All other beta releases are created by cherry-picking individual commits.

  - Create a new branch `bump-{version}` (for example `bump-2026.6.0b2`) from `rc`.
  - Cherry-pick all not-yet-picked PRs in the cycle milestone (`2026.6.0`).
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `rc`.

The first stable release:

  - Create a new branch `bump-{version}` (for example `bump-2026.6.0`) from `release`.
  - Merge `rc` into the new branch.
  - Cherry-pick any remaining not-yet-picked PRs in the cycle milestone (`2026.6.0`).
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `release`.

All other stable releases:

  - Create a new branch `bump-{version}` (for example `bump-2026.6.1`) from `release`.
  - Cherry-pick all PRs included in the milestone `{version}`.
  - Bump version on that branch to `{version}` using `script/bump-version.py` and commit.
  - Create a GitHub PR from `bump-{version}` to `release`.

The same is repeated for `esphome.io` with `s/dev/next/` and `s/release/current/`

## Milestone completeness check

Before a release is confirmed, the script verifies that **every merged PR on the
`{version}` milestone is actually present in the `bump-{version}` branch**. This
guards against commits being left behind — historically a couple of PRs that were
merged and milestoned still missed a release because a cherry-pick was skipped or
commits stayed in a beta.

The check compares the merged PR numbers on the milestone against the PR numbers
reachable in `git log {base}..bump-{version}` and warns about any that are
missing, then asks for confirmation before continuing.

You can also run it standalone against an already-tagged release:

```bash
$ esphomerelease verify-milestone 1.15.0 --base 1.14.5
```

## PR Merging and Releasing

The PR on GitHub can be used to let the CI check if everything's ok. When ready, merge the PR (DO NOT SQUASH, branch protection rules should already disallow this).

Then comes the second step: Tagging the release and publishing it.

  - Set `branch = 'rc' if is_beta_release else 'release'`
  - Create a GitHub release for branch `$branch` tagged `v{version}`, `prerelease=is_beta_release` and publish it.

GitHub Actions will automatically pick up the release, build assets and publish them to registries.

The same is repeated for the docs repo but again with different branch names.

## Home Assistant Add-On release

The Home Assistant Add-On also needs to be updated with the latest version (this is currently done on the maintainer side, in the future this could be automatic with webhooks).

Run `script/bump-version.py [--beta] [--stable]` in the https://github.com/esphome/home-assistant-addon repository.

Push the change, and tag the release `v{version}` in the GitHub releases interface.

## Running `esphomerelease`

Install with `pip3 install -e .` (preferably in a venv)

To create a release, do:

 - `esphomerelease cut {version}` - cuts the release and creates PRs in the esphome repos
 - Merge the PRs the step above created.
 - `esphomerelease publish {version}` - creates a tag and publishes the release.

For example some releases for 2026.6 could look like this:

```bash
$ esphomerelease cut 2026.6.0b1
# Check the PRs (auto opened in browser)
$ esphomerelease publish 2026.6.0b1

# To create release 2026.6.0b2:
# Add PRs to the `2026.6.0` cycle milestone (opened when 2026.5.0 was released).
# Then run
$ esphomerelease cut 2026.6.0b2
# Check release PRs
$ esphomerelease publish 2026.6.0b2

# Now we want to publish 2026.6.0
$ esphomerelease cut 2026.6.0
# Check release PRs
$ esphomerelease publish 2026.6.0

# Now create a patch release 2026.6.1
# Once again add PRs to the milestone 2026.6.1, then perform these steps:
$ esphomerelease cut 2026.6.1
# Check release PRs
$ esphomerelease publish 2026.6.1
```
