"""Pure helpers for choosing the changelog body used in release PRs and releases.

Import-clean: depends only on :mod:`esphomerelease.model` (stdlib + typing). It
imports nothing from ``.config`` or ``.project``, so it is unit-testable without a
real ``config.json`` or the sibling repository checkouts that the release tooling
otherwise requires.

The release flow (see :mod:`esphomerelease.cutting`) substitutes a link to the
website changelog instead of an inline changelog in two cases:

* the changelog is generated for the first beta / first stable release of a
  minor series (no meaningful diff to summarise yet for the core project), or
* the generated changelog is too long for a GitHub PR / release body.

Both decisions and the URL construction used to be duplicated inline in
``_create_prs`` and ``_publish_release``; they live here so the behaviour has a
single definition and a test.
"""

from .model import Version

# GitHub rejects PR / release bodies beyond a size limit. When a generated
# changelog exceeds this many characters, the release tooling substitutes a link
# to the changelog page on the website instead of embedding it inline.
MAX_CHANGELOG_LENGTH = 65000


def changelog_website_url(version: Version) -> str:
    """Return the canonical website changelog URL for ``version``.

    The changelog page is keyed by the ``major.minor.0`` stable version (patch,
    beta and dev components stripped). Beta releases point at the beta subdomain.
    """
    changelog_version = version.replace(patch=0, beta=0, dev=False)
    domain = "beta.esphome.io" if version.beta else "esphome.io"
    return f"https://{domain}/changelog/{changelog_version}.html"


def use_website_link_for_release(version: Version, *, is_primary_project: bool) -> bool:
    """Whether to use the website link instead of generating an inline changelog.

    The first beta (``bN`` == 1) and the first stable release of a minor series
    (``patch`` == 0, no beta) skip inline changelog generation for the primary
    (esphome core) project and link to the website instead. Other projects and
    later releases always generate the changelog inline.
    """
    if not is_primary_project:
        return False
    is_first_beta = version.beta == 1
    is_first_main_release = version.patch == 0 and version.beta == 0
    return is_first_beta or is_first_main_release


def changelog_too_long(changelog_md: str) -> bool:
    """Whether ``changelog_md`` exceeds the GitHub body length limit."""
    return len(changelog_md) > MAX_CHANGELOG_LENGTH
