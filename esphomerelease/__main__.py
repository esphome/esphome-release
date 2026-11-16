import sys

from esphomerelease.exceptions import ConfigNotFoundError


# pylint: disable=unused-argument
def main(*args):
    # Imported here because importing commands loads config.json from the cwd.
    try:
        from esphomerelease.commands import cli  # noqa: PLC0415
    except ConfigNotFoundError as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)
    # pylint: disable=no-value-for-parameter
    cli()


if __name__ == "__main__":
    main()
