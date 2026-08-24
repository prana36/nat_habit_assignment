#!/usr/bin/env sh
set -eu

echo "Build and run TaskFlow with Docker Compose"
docker compose up --build -d
echo "API should be available at http://localhost:8000"
