# ADR 0008: VM power without a plan

Date: 2026-10-24 · Accepted (Elfaz: "ok to run power action without a plan").

## Problem

Restarting or powering off a VM meant opening its console and typing, or Pause and Resume, which are two plans to approve for something that changes nothing in the spec.

## Decision

A new job action, `power` (contract version 3), carries `power: {op, vms}`:

| op | kubectl |
|---|---|
| `start` | `patch virtualmachine <vm> --type merge -p {"spec":{"runStrategy":"Always"}}` |
| `stop` | `patch … runStrategy Halted` (KubeVirt shuts the guest down gracefully) |
| `restart` | `delete virtualmachineinstance <vm> --ignore-not-found --wait=false` (KubeVirt starts it again from the VM, picking up template changes) |
| `force-stop` | Halted, then `delete virtualmachineinstance … --grace-period=0 --force` |
| `force-restart` | `delete virtualmachineinstance … --grace-period=0 --force` |

- **No plan and no gate.** Power changes no object the spec describes beyond a VM's run state, and a person queues each job in xStage, which records who asked and needs the build-and-operate right in the environment's stage.
- **Narrow.** The worker acts only on VirtualMachines that its render of the job's spec contains (`vms` empty: all of them), and only with the calls above. Any other name fails the job before anything runs.
- **The spec still wins.** A later apply sets each VM's run state back to what the spec says. Pause, which is a spec change through a plan, is the way to keep VMs off.

This narrows the rule "every `kubectl apply` or `delete` goes through `gate.check`" with one written exception: setting `runStrategy` on, or deleting the running instance of, a VM the environment renders.
