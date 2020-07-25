import os
import subprocess
import time
import threading
import queue
from typing import TYPE_CHECKING

import click
import requests

from .config import CONFIG
from .model import Version
from .exceptions import EsphomeReleaseError


if TYPE_CHECKING:
    from .project import Project


def copy_clipboard(text):
    """Copy some text to clipboard.

    Used for inserting changelog to clipboard.
    """
    if subprocess.run('pbcopy', input=text.encode()).returncode != 0:
        print("---------- START COPY ----------")
        print(text)
        print("---------- STOP COPY ----------")


def gprint(s, *args, fg='green'):
    """Print with green text."""
    click.secho(s.format(*args), fg=fg)


def wait_for_netlify(version: Version):
    """Wait for netlify release build to be live."""
    gprint("Waiting for netlify build!")
    start = time.time()
    while True:
        url = f"https://{'beta.' if version.beta else ''}esphome.io/_static/version"
        req = requests.get(url)
        if req.content.decode() == str(version):
            break
        print(f"Waiting for netlify: {req.content} != {version}")
        time.sleep(30)

    gprint(f"Netlify build took {(time.time() - start) / 60:.0f} minutes")


def purge_cloudflare_cache():
    """Purge cloudflare cache.

    Used after netlify release build finishes so that users
    see new release immediately and don't have to wait for the cache to clear
    """
    if 'cloudflare_email' not in CONFIG:
        gprint("Skipping purging cloudflare cache")
        return

    gprint("Purging cloudflare cache!")
    headers = {
        'X-Auth-Email': CONFIG['cloudflare_email'],
        'X-Auth-Key': CONFIG['cloudflare_auth_key'],
        'Content-Type': 'application/json',
    }
    zone = CONFIG['cloudflare_zone']
    requests.post(
        f'https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache',
        headers=headers, data='{"purge_everything": true}')


def process_asynchronously(jobs, heading: str = None, num_threads: int = os.cpu_count()) -> str:
    """Run a list of function objects asynchronously in a threa pool and return the result as a list."""
    result = {}
    q = queue.Queue(maxsize=num_threads)

    def worker():
        while True:
            item = q.get()
            if item is None:
                q.task_done()
                break

            num, job = item
            result[num] = job()
            q.task_done()

    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    job_list = list(enumerate(jobs)) + [None] * num_threads
    with click.progressbar(job_list, label=heading) as bar:
        for item in bar:
            q.put(item)
        q.join()

    for t in threads:
        t.join()

    return [result[i] for i, job in enumerate(jobs)]


def confirm_replace(prj: 'Project'):
    """Confirm a diff before committing."""
    gprint("=============== DIFF START ===============")
    prj.run_git('add', '.')
    prj.run_git('diff', '--color', '--cached', show=True)
    if not click.confirm(click.style("==== Please verify the diff is correct ====", fg='green')):
        raise EsphomeReleaseError


def update_local_copies():
    """Update local repos to be up to date with remotes."""
    from .project import EsphomeDocsProject, EsphomeProject, EsphomeHassioProject

    if EsphomeProject.has_local_changes:
        raise EsphomeReleaseError("Local changes in esphome repository!")
    if EsphomeDocsProject.has_local_changes:
        raise EsphomeReleaseError("Local changes in esphome-docs repository!")

    gprint("Updating local repo copies")
    for branch in ['master', 'dev', 'beta']:
        EsphomeProject.checkout_pull(branch)
    for branch in ['current', 'next', 'beta']:
        EsphomeDocsProject.checkout_pull(branch)

    with EsphomeDocsProject.workon('next'):
        EsphomeDocsProject.merge('current')
    with EsphomeDocsProject.workon('beta'):
        EsphomeDocsProject.merge('current')

    EsphomeHassioProject.checkout_pull('master')


def checkout_dev():
    from .project import EsphomeDocsProject, EsphomeProject

    gprint("Checking out dev again...")
    EsphomeProject.checkout('dev')
    EsphomeDocsProject.checkout('next')
