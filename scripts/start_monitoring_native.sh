#!/usr/bin/env bash
# Fallback if Docker is not available: downloads and runs Prometheus + node_exporter natively.
# Grafana is installed separately with apt (see README, "Option B").
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p bin logs

PROM_V=3.5.0
NODE_V=1.9.1

if [ ! -x bin/prometheus-$PROM_V.linux-amd64/prometheus ]; then
  echo "Downloading Prometheus $PROM_V ..."
  curl -sSL https://github.com/prometheus/prometheus/releases/download/v$PROM_V/prometheus-$PROM_V.linux-amd64.tar.gz | tar xz -C bin
fi
if [ ! -x bin/node_exporter-$NODE_V.linux-amd64/node_exporter ]; then
  echo "Downloading node_exporter $NODE_V ..."
  curl -sSL https://github.com/prometheus/node_exporter/releases/download/v$NODE_V/node_exporter-$NODE_V.linux-amd64.tar.gz | tar xz -C bin
fi

nohup bin/node_exporter-$NODE_V.linux-amd64/node_exporter > logs/node_exporter.log 2>&1 &
echo "node_exporter started on :9100 (pid $!)"
nohup bin/prometheus-$PROM_V.linux-amd64/prometheus --config.file=prometheus.yml \
  --storage.tsdb.path=bin/prometheus-data --web.enable-lifecycle > logs/prometheus.log 2>&1 &
echo "Prometheus started on :9090 (pid $!)"
echo "Stop both with:  pkill -f node_exporter-$NODE_V; pkill -f prometheus-$PROM_V"
