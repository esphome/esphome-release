import shlex
import subprocess
import sys

import click

from .config import CONFIG

def execute_command(*args, **kwargs) -> bytes:
    from .util import EsphomeReleaseError

    silent = kwargs.pop('silent', False)
    full_cmd = ' '.join(shlex.quote(x) for x in args)
    if not silent:
        if 'cwd' in kwargs:
            cwd = kwargs['cwd']
            print(f"Running: {full_cmd} (cwd={cwd})")
        else:
            print(f"Running: {full_cmd}")

        if CONFIG['step']:
            while not click.confirm("Run command?"):
                continue

    show = kwargs.pop('show', False)
    live = kwargs.pop('live', False)
    kwargs.setdefault('stdout', subprocess.PIPE)
    kwargs.setdefault('stderr', subprocess.PIPE)
    fail_ok = kwargs.pop('fail_ok', False)

    if live:
        kwargs['stdout'] = subprocess.PIPE
        kwargs['stderr'] = subprocess.STDOUT
        process = subprocess.Popen(args, **kwargs)
        while True:
            out = process.stdout.readline().decode()
            sys.stdout.write(out)
            sys.stdout.flush()
            if process.poll() is not None:
                break
    else:
        process = subprocess.run(args, **kwargs)

        if show:
            print(process.stdout.decode())

    if process.returncode != 0:
        if not silent or not fail_ok:
            print("stderr: ")
        if process.stderr is None:
            raise EsphomeReleaseError
        click.secho(process.stderr.decode(), fg='red')

        if not fail_ok:
            print(f"Failed running command {full_cmd}")
            print("Please try running it again")
            if click.confirm(click.style("If it passes, you press y", fg='red')):
                return process.stdout

        raise EsphomeReleaseError('Failed running command!')

    return process.stdout


def execute_git(project, *args, **kwargs) -> bytes:
    args = ['git', '-C', str(project.path), *args]
    return execute_command(*args, **kwargs)
