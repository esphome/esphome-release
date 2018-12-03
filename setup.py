from setuptools import setup

setup(
    name='esphomerelease',
    version='1.0',
    packages=['esphomerelease'],
    install_requires=['github3.py', 'click'],
    entry_points={
        'console_scripts': ['esphomerelease = esphomerelease.__main__:main']
    },
)
