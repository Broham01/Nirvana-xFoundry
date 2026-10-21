#!/bin/zsh
# Double-click to start the xFoundry worker in this window. Keep the window open while you use xStage's
# Environments page; close it (or press Ctrl-C) to stop. A job waiting for your approval resumes next time.
cd "${0:A:h}" || exit 1
platform/dev/worker.sh
status=$?
echo
read -k1 "?The worker stopped (exit $status). Press any key to close."
