# ESPHome Release Scripts

This repo contains ESPHome's 90% automated release scripts.

See [NOTES.md](NOTES.md) for more information on the release process.

To install use the command `pip3 install -e .`

The scripts use a configuration file with some secrets in the `config.json` file.

Run `cp config.{sample.,}json` and edit `config.json`. You need to generate a GitHub personal access token at https://github.com/settings/tokens (scopes: repo).
