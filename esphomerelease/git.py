import os
import shlex
import subprocess
import sys

import click

from esphomerelease.util import EsphomeReleaseError, EsphomeyamlProject, EsphomedocsProject


def execute_command(*args, **kwargs):
    if 'cwd' in kwargs:
        print("Running: {} (cwd={})".format(' '.join(shlex.quote(x) for x in args), kwargs['cwd']))
    else:
        print("Running: {}".format(' '.join(shlex.quote(x) for x in args)))
    show = kwargs.pop('show', False)
    live = kwargs.pop('live', False)
    kwargs.setdefault('stdout', subprocess.PIPE)
    kwargs.setdefault('stderr', subprocess.PIPE)

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
        print("stderr: ")
        if process.stderr is None:
            raise EsphomeReleaseError
        click.secho(process.stderr.decode(), fg='red')
        raise EsphomeReleaseError('Failed running command!')

    return process.stdout


def execute_git(project, *args, **kwargs):
    args = ['git', '-C', str(project.get_path()), *args]
    return execute_command(*args, **kwargs)


def get_esphomeyaml_version(branch):
    stdout = execute_git(EsphomeyamlProject, 'show', '{}:esphomeyaml/const.py'.format(branch))

    locals = {}
    exec(stdout, {}, locals)
    return locals['__version__']


def get_log(project, from_, to_):
    if project is EsphomedocsProject and to_ == 'dev':
        to_ = 'next'
    if project is EsphomedocsProject and to_ == 'master':
        to_ = 'current'
    if from_[0].isdigit():
        from_ = 'v' + from_
    args = ['log', '{}...{}'.format(from_, to_),
            "--pretty=format:- %s (%ae)", '--reverse']
    stdout = execute_git(project, *args)

    output = stdout.decode('utf-8')
    last = None

    for line in output.split('\n'):
        if line == last:
            continue
        last = line
        yield line


def fetch(path):
    execute_git(path, 'fetch')


def cherry_pick(path, sha):
    execute_git(path, 'cherry-pick', sha)
