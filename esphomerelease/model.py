from distutils.version import StrictVersion
import re

from .git import get_log


class LogLine:
    PR_PATTERN = re.compile('\(#(\d+)\)')

    def __init__(self, line):
        # Strip off the '-' at the start
        parts = line.split()[1:]

        self.line = line
        self.email = parts.pop()[1:-1]

        pr_match = self.PR_PATTERN.match(parts[-1])

        if pr_match:
            self.pr = int(pr_match.groups(1)[0])
            parts.pop()
        else:
            self.pr = None

        self.message = ' '.join(parts)


class Release:
    def __init__(self, version, source, target):
        self.version = StrictVersion(version)
        self.source = source
        self.target = target
        self._log_lines = {}
        self._repos = {}

        if self.version.version[-1] == 0 and not self.version.prerelease:
            vstring = '-'.join(map(str, self.version.version[:2]))
        else:
            vstring = '-'.join(map(str, self.version.version))
        self.identifier = 'release-' + vstring

        if self.version.prerelease:
            pstring = ''.join(map(str, self.version.prerelease))
            self.identifier = self.identifier + pstring

    @property
    def is_patch_release(self):
        """Return if this is a patch release or not.

        Patch release is when X in 0.0.X is not 0.
        """
        return self.version.version[-1] != 0

    def log_lines(self, project):
        if project.name not in self._log_lines:
            lines = [LogLine(line) for line in get_log(project, self.source, self.target)]
            self._log_lines[project.name] = lines
        return self._log_lines[project.name]
