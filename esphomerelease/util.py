import os
import subprocess
import time
import threading
import queue
import shlex
import sys

import click
import requests

from .config import CONFIG
from .model import Version
from .exceptions import EsphomeReleaseError


def copy_clipboard(text):
    """Copy some text to clipboard.

    Used for inserting changelog to clipboard.
    """
    if subprocess.run("pbcopy", input=text.encode()).returncode != 0:
        print("---------- START COPY ----------")
        print(text)
        print("---------- STOP COPY ----------")


def open_vscode(*paths):
    subprocess.run(["code", *paths])


def gprint(s, *args, fg="green"):
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
    if "cloudflare_email" not in CONFIG:
        gprint("Skipping purging cloudflare cache")
        return

    gprint("Purging cloudflare cache!")
    headers = {
        "X-Auth-Email": CONFIG["cloudflare_email"],
        "X-Auth-Key": CONFIG["cloudflare_auth_key"],
        "Content-Type": "application/json",
    }
    zone = CONFIG["cloudflare_zone"]
    requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache",
        headers=headers,
        data='{"purge_everything": true}',
    )


def process_asynchronously(
    jobs, heading: str = None, num_threads: int = os.cpu_count()
) -> str:
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


def update_local_copies():
    """Update local repos to be up to date with remotes."""
    from .project import EsphomeDocsProject, EsphomeProject, EsphomeHassioProject

    if EsphomeProject.has_local_changes:
        raise EsphomeReleaseError("Local changes in esphome repository!")
    if EsphomeDocsProject.has_local_changes:
        raise EsphomeReleaseError("Local changes in esphome-docs repository!")

    gprint("Updating local repo copies")
    for branch in ["release", "dev", "beta"]:
        EsphomeProject.checkout_pull(branch)
    for branch in ["current", "next", "beta"]:
        EsphomeDocsProject.checkout_pull(branch)

    with EsphomeDocsProject.workon("next"):
        EsphomeDocsProject.merge("current")
    with EsphomeDocsProject.workon("beta"):
        EsphomeDocsProject.merge("current")

    EsphomeHassioProject.checkout_pull("main")


def checkout_dev():
    from .project import EsphomeDocsProject, EsphomeProject

    gprint("Checking out dev again...")
    EsphomeProject.checkout("dev")
    EsphomeDocsProject.checkout("next")


def random_quote():
    # Idea from @frenck here: https://github.com/home-assistant/core/pull/38065
    try:
        js = requests.get("http://quotes.stormconsultancy.co.uk/random.json").json()
        quote = js["quote"]
        author = js["author"]
        return f'\n> _"{quote}"_\n\n~ {author}\n'
    except Exception:  # pylint: disable=broad-except
        return ""


def confirm(text):
    while not click.confirm(text):
        pass


def execute_command(*args, **kwargs) -> bytes:
    """Execute an external program given by `args` and return the result stdout.

    show: Show the stdout output
    live: Directly print all command output to stdout
    on_fail: Optional callback to call when returncode is non-zero
    fail_ok: If the command is allowed to fail, else notifies the user
    silent: Don't print anything about this command.
    other kwargs passed to subprocess.run
    """
    silent = kwargs.pop("silent", False)
    full_cmd = " ".join(shlex.quote(x) for x in args)
    if not silent:
        if "cwd" in kwargs:
            cwd = kwargs["cwd"]
            print(f"Running: {full_cmd} (cwd={cwd})")
        else:
            print(f"Running: {full_cmd}")

        if CONFIG["step"]:
            while not click.confirm("Run command?"):
                continue

    show = kwargs.pop("show", False)
    live = kwargs.pop("live", False)
    on_fail = kwargs.pop("on_fail", None)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    fail_ok = kwargs.pop("fail_ok", False)

    if live:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
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
        click.secho(process.stderr.decode(), fg="red")

        if not fail_ok:
            if on_fail is not None:
                return on_fail(process.stdout)
            print(f"Failed running command {full_cmd}")
            print("Please try running it again")
            if click.confirm(click.style("If it passes, you press y", fg="red")):
                return process.stdout

        raise EsphomeReleaseError("Failed running command!")

    return process.stdout
