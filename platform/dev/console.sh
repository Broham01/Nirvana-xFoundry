#!/usr/bin/env bash
# VM consoles and logs in the browser (ADR 0005, 0006). Serves noVNC, a log page and a narrow proxy to the cluster on 127.0.0.1:$CONSOLE_PORT:
#   http://localhost:8011/console/vnc.html?autoconnect=1&path=/k8s/apis/subresources.kubevirt.io/v1/namespaces/<ns>/virtualmachineinstances/<vm>/vnc
#   http://localhost:8011/console/logs.html?ns=<namespace>&appliance=<name>   (or &pod=<pod>)
# The proxy uses the view-only vm-console identity (platform/console-rbac.yaml), never xFoundry's own credentials, accepts
# only GET (a console is a GET upgraded to a WebSocket) on the VNC and serial-console paths, a namespace's pod list and a
# pod's log, and answers only to localhost.
# kubectl proxy checks paths after removing /k8s, so the filter accepts them with or without it.
# xStage's supervisor runs this for you; stop it with Ctrl-C.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
WWW="$DEV_DIR/.novnc/$NOVNC_VERSION"
[ -f "$WWW/vnc.html" ] || { echo "noVNC is not downloaded yet: run setup again (platform/dev/up.sh)."; exit 2; }
k get --raw /readyz --request-timeout=10s >/dev/null 2>&1 || { echo "The dev platform is not running (its cluster does not answer)."; exit 3; }
TOKEN=$(k -n xfoundry-system get secret vm-console-token -o jsonpath='{.data.token}' 2>/dev/null | base64 --decode || true)
[ -n "$TOKEN" ] || { echo "The console identity is missing: run setup again (platform/dev/up.sh)."; exit 2; }
SERVER=$(k config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA=$(k config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
CONF="$DEV_DIR/.console-kubeconfig"
umask 077
cat > "$CONF" <<CONF
apiVersion: v1
kind: Config
clusters: [{name: xfoundry, cluster: {server: "$SERVER", certificate-authority-data: "$CA"}}]
users: [{name: vm-console, user: {token: "$TOKEN"}}]
contexts: [{name: vm-console, context: {cluster: xfoundry, user: vm-console}}]
current-context: vm-console
CONF
# The log page is served next to noVNC (the proxy serves one folder).
cp "$(dirname "$0")/logs.html" "$WWW/logs.html"
echo "VM consoles on http://localhost:$CONSOLE_PORT/console/ (view-only identity)."
exec kubectl --kubeconfig "$CONF" proxy --address 127.0.0.1 --port "$CONSOLE_PORT" \
  --api-prefix=/k8s/ --www="$WWW" --www-prefix=/console/ \
  --accept-paths='^(/k8s)?/apis/subresources\.kubevirt\.io/v1/namespaces/[^/]+/virtualmachineinstances/[^/]+/(vnc|console)$,^(/k8s)?/api/v1/namespaces/[^/]+/pods(/[^/]+/log)?$' \
  --reject-methods='^(POST|PUT|PATCH|DELETE|OPTIONS)$'
