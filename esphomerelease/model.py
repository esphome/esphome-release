import re
import enum
from dataclasses import dataclass, replace
from typing import Union


class Branch(enum.Enum):
    STABLE = 'stable'
    BETA = 'beta'
    DEV = 'dev'


BranchType = Union[str, Branch]


@dataclass(eq=True, frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    beta: int = 0
    dev: bool = False

    def __str__(self):
        return f'{self.major}.{self.minor}.{self.full_patch}'

    @property
    def full_patch(self):
        res = f'{self.patch}'
        if self.beta > 0:
            res += f'b{self.beta}'
        if self.dev:
            res += '-dev'
        return res

    @classmethod
    def parse(cls, value):
        match = re.match(r'(\d+).(\d+).(\d+)(b\d+)?(-dev)?', value)
        if match is None:
            raise ValueError(f"Could not parse version {value}")
        major = int(match[1])
        minor = int(match[2])
        patch = int(match[3])
        beta = 0
        if match[4]:
            beta = int(match[4][1:])
            if beta == 0:
                raise ValueError("Beta version always should have value >0")
        dev = bool(match[5])
        if beta and dev:
            raise ValueError("Can't be both a beta and dev version")
        return Version(
            major=major, minor=minor, patch=patch,
            beta=beta, dev=dev
        )

    def replace(self, **kwargs) -> 'Version':
        """Replace some values of this version, does not change self."""
        return replace(self, **kwargs)

    @property
    def next_dev_version(self):
        return self.replace(
            minor=self.minor+1,
            patch=0,
            beta=0,
            dev=True,
        )

    @property
    def next_beta_version(self):
        return self.replace(beta=self.beta+1)

    @property
    def previous_beta_version(self):
        if self.beta == 0:
            raise ValueError(f"No previous beta version for {self}")
        return self.replace(beta=self.beta-1)

    @property
    def next_patch_version(self):
        return self.replace(
            patch=self.patch+1,
            beta=0,
            dev=False
        )

    @property
    def previous_patch_version(self):
        if self.patch == 0:
            raise ValueError(f"No previous patch version for {self}")
        return self.replace(path=self.patch-1)

    def __lt__(self, other: 'Version') -> bool:
        # 1.14.5 < 2.0.0
        if self.major != other.major:
            return self.major < other.major
        # 1.14.5 < 1.15.0
        if self.minor != other.minor:
            return self.minor < other.minor
        # 1.14.5 < 1.14.6
        if self.patch != other.patch:
            return self.patch < other.patch
        # 1.15.0-dev < 1.15.0
        if self.dev is not other.beta:
            return self.dev
        # 1.15.0b1 < 1.15.0
        if self.beta != other.beta:
            # 1.15.0b1 < 1.15.0
            if 0 in (self.beta, other.beta):
                return other.beta == 0
            # 1.15.0b1 < 1.15.0b2
            return self.beta < other.beta
        if self.beta < other.beta:
            return True

        assert self == other
        return False

    def __le__(self, other) -> bool:
        return self < other or self == other

    def __gt__(self, other) -> bool:
        return other < self

    def __ge__(self, other) -> bool:
        return self > other or self == other
