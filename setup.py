#!/usr/bin/env python3
import os

from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, 'requirements.txt')) as requirements_txt:
    REQUIRES = requirements_txt.read().splitlines()

setup(
    name='esphomerelease',
    version='1.0',
    packages=['esphomerelease'],
    install_requires=REQUIRES,
    entry_points={
        'console_scripts': ['esphomerelease = esphomerelease.__main__:main']
    },
)
