from datetime import datetime

from github3 import GitHub

from .config import CONFIG


GITHUB_SESSION = None


def get_session() -> GitHub:
    global GITHUB_SESSION

    if GITHUB_SESSION is not None:
        return GITHUB_SESSION

    token = CONFIG['github_token']

    gh = GitHub(token=token)
    rate_limit = gh.rate_limit()['rate']
    limit = rate_limit['limit']
    remaining = rate_limit['remaining']
    reset = datetime.utcfromtimestamp(rate_limit['reset'])
    print(f"{remaining}/{limit} rate limit remaining")
    print(f"Reset at {reset} UTC (in {reset - datetime.utcnow()})")
    GITHUB_SESSION = gh
    return GITHUB_SESSION
