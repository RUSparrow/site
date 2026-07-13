#!/bin/bash

cd /home/sparrow/site

git pull

cd /home/sparrow/site/server-monitor

docker compose up -d --build
