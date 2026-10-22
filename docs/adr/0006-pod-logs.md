# ADR 0006: Read-only pod logs in the browser

Date: 2026-10-22 · Accepted. Nirvana xStage [ADR 0018](https://github.com/Broham01/Nirvana-xStage/blob/main/docs/adr/0018-app-switch-and-new-tabs.md) (its Workloads tab links here).

Appliances that run as pods (FRR, HAProxy and the other container products) have no screen, so the VM console (ADR 0005) does not help with them. Their logs now open in a browser tab from xStage's Workloads tab and environment page.

- `platform/dev/logs.html` is served by `console.sh` next to noVNC, at `/console/logs.html?ns=<namespace>&appliance=<name>` (or `&pod=<pod>`). It lists the appliance's pods by the `xfoundry.io/appliance` label and streams the chosen container's log (`follow`, last 1000 lines, timestamps). Log text is added as text nodes, never HTML.
- The same view-only `vm-console` identity reads them. It gains `get` and `list` on `pods` and `pods/log`, and nothing else: it still cannot exec, attach, port-forward, create, change or delete.
- The proxy's accept list gains exactly two paths, `/api/v1/namespaces/<ns>/pods` and `/api/v1/namespaces/<ns>/pods/<pod>/log`, still GET only and still on 127.0.0.1.

Run setup again (`platform/dev/up.sh`, or **Run setup again** in xStage) once, so the identity gets the new permissions.
