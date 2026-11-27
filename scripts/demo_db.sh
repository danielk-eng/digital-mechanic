#!/usr/bin/env bash
# Demo state of the deb stand (docs/ВИДЕО_СЦЕНАРИЙ.md). Run in stand/app on deb.
#   scripts/demo_db.sh backup NAME     online copy of var/app.db -> var/demo-NAME.db (app keeps running)
#   scripts/demo_db.sh restore NAME    stop app, var/demo-NAME.db -> var/app.db, start app (~10 s)
# «clean» = the state right after scripts/demo_prep.py: every scene works on the first try. Restoring it drops
# everything written to the stand after the snapshot (chats, tickets, repairs): it is a demo stand.
set -euo pipefail
cd "$(dirname "$0")/.."
P=${COMPOSE_PROJECT:-iikuznitsa}
cmd=${1:-}; name=${2:-clean}
case "$cmd" in
  backup)
    docker compose -p "$P" exec -T app python -c "import sqlite3; s=sqlite3.connect('var/app.db'); d=sqlite3.connect('var/demo-$name.db'); s.backup(d); d.close(); print('ok')"
    ls -la "var/demo-$name.db" ;;
  restore)
    test -f "var/demo-$name.db" || { echo "no var/demo-$name.db" >&2; exit 1; }
    docker compose -p "$P" stop app >/dev/null
    # var/ belongs to root (the container writes it): copy inside a throwaway app container
    docker compose -p "$P" run --rm --no-deps -T --entrypoint sh app -c "cp var/demo-$name.db var/app.db && rm -f var/app.db-wal var/app.db-shm"
    docker compose -p "$P" start app >/dev/null
    for i in $(seq 1 30); do sleep 2; curl -fsS "127.0.0.1:${PORT:-18610}/api/health" >/dev/null 2>&1 && { echo "restored $name"; exit 0; }; done
    echo "app did not come back" >&2; exit 1 ;;
  *) echo "usage: $0 backup|restore NAME" >&2; exit 2 ;;
esac
