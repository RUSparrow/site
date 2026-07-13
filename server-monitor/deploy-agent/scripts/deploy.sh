#!/bin/bash

set -e

cd /home/sparrow/site

git config --global --add safe.directory /home/sparrow/site

git pull

cd /home/sparrow/site/server-monitor

docker compose up -d --build --no-deps monitor
