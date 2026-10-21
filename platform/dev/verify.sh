#!/usr/bin/env bash
# Checks an applied environment end to end (default: dev-lab). Changes nothing in the environment; the ping runs from
# a throwaway debug container attached to the load balancer pod.
#   platform/dev/verify.sh [environment-name]
# 1. appliance pods are ready and every VM is Running;
# 2. each VM has an address on its subnet (from Kube-OVN's IP records);
# 3. a ping from the load balancer (front subnet) reaches each VM (app subnet): that path only works if the router
#    forwards and the load balancer routes the app subnet through it.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
env_name=${1:-dev-lab}; ns="xf-$env_name"; ok=0
pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$1"; ok=1; }

echo "Environment $env_name (namespace $ns)"
k get ns "$ns" >/dev/null 2>&1 || { fail "namespace $ns not found: apply the environment first"; exit 1; }

echo "Appliances"
for d in $(k -n "$ns" get deploy -o name); do
  if k -n "$ns" rollout status "$d" --timeout=120s >/dev/null 2>&1; then pass "${d#*/} ready"; else fail "${d#*/} not ready"; fi
done

echo "VMs"
vms=$(k -n "$ns" get vm -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
for vm in $vms; do
  phase=$(k -n "$ns" get vmi "$vm" -o jsonpath='{.status.phase}' 2>/dev/null)
  [ "$phase" = Running ] && pass "$vm Running" || fail "$vm ${phase:-has no instance yet} (emulated VMs can take a few minutes; see: k -n $ns get dv,vmi)"
done

echo "Addresses and routing through the router"
lb=$(k -n "$ns" get pod -l xfoundry.io/role=load-balancer -o name 2>/dev/null | head -1)
[ -n "$lb" ] || echo "  (no load balancer pod in this environment: skipping the routed ping)"
for vm in $vms; do
  ip=$(k get ips -o json 2>/dev/null | python3 -c "
import json, sys
for i in json.load(sys.stdin)['items']:
    s = i['spec']
    if s.get('namespace') == '$ns' and s.get('podName', '').startswith('virt-launcher-$vm-') and s.get('subnet', '').startswith('$ns-'):
        print(s.get('v4IpAddress') or s.get('ipAddress', '').split(',')[0]); break")
  if [ -z "$ip" ]; then fail "$vm has no address on an environment subnet yet"; continue; fi
  pass "$vm has $ip"
  if [ -n "$lb" ]; then
    if k -n "$ns" debug "${lb#pod/}" --image=docker.io/library/busybox:1.37 --profile=netadmin -q --attach --rm -- \
         ping -c 3 -W 2 "$ip" >/dev/null 2>&1; then
      pass "${lb#pod/} reaches $vm ($ip) through the router"
    else
      fail "${lb#pod/} cannot reach $vm ($ip): check the router forwards (kubectl -n $ns exec deploy/rt01 -- sysctl net.ipv4.ip_forward) and the VM booted (virtctl console -n $ns $vm)"
    fi
  fi
done

if [ $ok -eq 0 ]; then
  echo; echo "All checks passed. Last exit check: plan the same spec again; Nirvana should show no changes:"
  echo "  xfoundry collect -o fresh.snapshot.json && xfoundry plan examples/$env_name.yaml --snapshot fresh.snapshot.json"
fi
exit $ok
