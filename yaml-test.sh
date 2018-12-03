#!/bin/bash

source /usr/local/bin/virtualenvwrapper.sh

workon yaml

set -x -e

cd ../esphomeyaml

flake8 esphomeyaml
pylint esphomeyaml --rcfile pylintrc
esphomeyaml tests/test1.yaml compile
esphomeyaml tests/test2.yaml compile
