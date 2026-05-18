#!/usr/bin/env bash
# Stub harness for integration tests. Accepts a mode argument.
# mode=happy  -> prints a line and exits 0
# mode=error  -> exits 17
# mode=long   -> sleeps indefinitely (killed by test teardown)
# mode=*      -> prints a line and exits 0

MODE="${1:-happy}"

case "$MODE" in
    error)
        exit 17
        ;;
    long)
        while true; do sleep 1; done
        ;;
    *)
        echo "stub-harness: mode=$MODE prompt=$2"
        exit 0
        ;;
esac
