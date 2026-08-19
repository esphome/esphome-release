# ESPHome Release Scripts

This repo contains ESPHome's 90% automated release scripts.

See [NOTES.md](NOTES.md) for more information on the release process.

To install use the command `pip3 install -e .`

The scripts use a configuration file with the local paths to the various repos in the `config.json` file.

Run `cp config.{sample.,}json` and edit `config.json`.

## GitHub authentication

The GitHub API calls authenticate with the token stored by the [GitHub CLI](https://cli.github.com/), which is read at runtime with `gh auth token`. Nothing needs to be added to `config.json`, so no GitHub secret is kept in a plaintext file in this folder and access is revoked centrally through `gh`.

Authenticate once with:

```bash
gh auth login
```

The token needs the `repo` scope to create releases and pull requests. If it is missing, the API answers with a 403 rather than anything obvious, so grant it with:

```bash
gh auth refresh -s repo
```

As a fallback, an optional `github_token` key in `config.json` is still honoured when `gh` cannot supply a token. Prefer the `gh` route: a personal access token in `config.json` sits on disk in plaintext.
