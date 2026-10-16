#!/usr/bin/env bash
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
MC_JAVA="${MC_JAVA:-java}"
MC_JAR="${MC_JAR:-server.jar}"
MC_XMS="${MC_XMS:-2G}"
MC_XMX="${MC_XMX:-4G}"
MC_RESTART_DELAY="${MC_RESTART_DELAY:-5}"
if [[ ! -r "$MC_JAR" || ! -r astrbot-spark-agent.jar ]]; then
  printf '%s\n' 'Set MC_JAR and put astrbot-spark-agent.jar in the server directory.' >&2
  exit 1
fi
trap 'exit 0' INT TERM
while true; do
  "$MC_JAVA" "-javaagent:$PWD/astrbot-spark-agent.jar" "-Xms$MC_XMS" "-Xmx$MC_XMX" -jar "$MC_JAR" nogui
  printf 'Server stopped. Restarting in %s seconds...\n' "$MC_RESTART_DELAY"
  sleep "$MC_RESTART_DELAY"
done
