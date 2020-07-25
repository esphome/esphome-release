import re
import enum
from dataclasses import dataclass, replace
from typing import Union


class Branch(enum.Enum):
    STABLE = 'stable'
    BETA = 'beta'
    DEV = 'dev'


BranchType = Union[str, Branch]


@dataclass
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
        assert match is not None
        major = int(match[1])
        minor = int(match[2])
        patch = int(match[3])
        beta = 0
        if match[4]:
            beta = int(match[4][1:])
            assert beta > 0
        dev = bool(match[5])
        assert not (beta and dev)
        return Version(
            major=major, minor=minor, patch=patch,
            beta=beta, dev=dev
        )

    @property
    def next_dev_version(self):
        return replace(
            self,
            minor=self.minor+1,
            patch=0,
            beta=0,
            dev=True,
        )

    @property
    def next_beta_version(self):
        assert self.beta != 0
        return replace(
            self,
            beta=self.beta+1
        )

    @property
    def next_patch_version(self):
        return replace(
            self,
            patch=self.patch+1,
            beta=0,
            dev=False
        )
