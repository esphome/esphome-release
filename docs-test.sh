#!/bin/bash

source /usr/local/bin/virtualenvwrapper.sh

cd ../esphomedocs
workon docs

set -x -e

make html
