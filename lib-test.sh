#!/bin/bash

source /usr/local/bin/virtualenvwrapper.sh

cd ../esphomelib
workon lib

set -x -e

pio run -e livingroom --disable-auto-clean
pio run -e livingroom8266 --disable-auto-clean
