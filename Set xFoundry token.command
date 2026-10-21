#!/bin/zsh
# Double-click to store the worker's service token in your macOS Keychain (item "nirvana-xfoundry-token").
# Create the token in xStage: Account > Service tokens. Paste it here when asked; it is not shown or saved anywhere else.
cd "${0:A:h}" || exit 1
echo "Paste the xStage service token for the xFoundry worker, then press Return (typing is hidden)."
echo "macOS asks twice: 'password data for new item' and 'retype password'."
if security add-generic-password -U -s nirvana-xfoundry-token -a "$USER" -l "Nirvana xFoundry worker token" -w; then
  echo "Saved. Now double-click \"Start xFoundry worker.command\"."
else
  echo "Not saved."
fi
read -k1 "?Press any key to close."
