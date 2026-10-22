# ADR 0005: VM consoles in the browser

Date: 2026-10-22 · Accepted. xStage [ADR 0017](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0017-vm-consoles-and-light-styles.md).

## Decision

`platform/dev/console.sh` serves noVNC (pinned in `versions.env`, downloaded by `up.sh` into the git-ignored `platform/dev/.novnc/`) and a `kubectl proxy` on `127.0.0.1:$CONSOLE_PORT` (8011):

- **Identity:** the `vm-console` ServiceAccount (`platform/console-rbac.yaml`): `get` on `virtualmachineinstances/vnc` and `/console`, `get`/`list` on VMIs. Never xFoundry's own kubeconfig. Its kubeconfig is written with mode 600 to the git-ignored `platform/dev/.console-kubeconfig`.
- **Filter:** only GET (a console is a GET upgraded to a WebSocket), only the VNC and serial-console paths, only from localhost.
- **Link:** `http://localhost:8011/console/vnc.html?autoconnect=1&path=/k8s/apis/subresources.kubevirt.io/v1/namespaces/<ns>/virtualmachineinstances/<vm>/vnc`.

xStage's supervisor runs it next to the worker and links to it from each running VM. A console is not a change: nothing is applied, so the gate is not involved.

## Consequences

- Run setup again once (`up.sh`) to add the identity and download noVNC.
- Pods (FRR, HAProxy) have no screen; `kubectl exec` stays a terminal action and the proxy refuses it.
