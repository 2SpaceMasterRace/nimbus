#!/usr/bin/env python3
"""
nimbus_iac.py — Terraform-style IaC for the Nimbus Fly.io deployment.

Usage:
  python nimbus_iac.py plan      # diff config vs Fly.io (read-only)
  python nimbus_iac.py apply     # create / update resources (idempotent)
  python nimbus_iac.py destroy   # delete all managed resources
  python nimbus_iac.py show      # print local state file
  python nimbus_iac.py output    # print app URL, machine IDs
  python nimbus_iac.py refresh   # sync state file against live Fly.io API

Required env var:
  FLY_API_TOKEN   — your Fly.io token (fly tokens create deploy -a <app>)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")


# ── File locations ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
STATE_FILE = _HERE / ".infra_state.json"
CONFIG_FILE = _HERE / "infra_config.yml"

# ── ANSI colours (Terraform-style output) ─────────────────────────────────────
_NO_COLOR = not sys.stdout.isatty() or os.environ.get("NO_COLOR")

def _c(code: str, s: str) -> str:
    return s if _NO_COLOR else f"\033[{code}m{s}\033[0m"

def green(s: str)  -> str: return _c("32", s)
def red(s: str)    -> str: return _c("31", s)
def yellow(s: str) -> str: return _c("33", s)
def cyan(s: str)   -> str: return _c("36", s)
def bold(s: str)   -> str: return _c("1",  s)


# ── Config & state helpers ─────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        sys.exit(f"{red('Error:')} config file not found: {CONFIG_FILE}")
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"resources": {}, "outputs": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  {cyan('→')} State saved → {STATE_FILE.name}")


# ── Fly.io API client ──────────────────────────────────────────────────────────
MACHINES_API = "https://api.machines.dev"


class FlyClient:
    def __init__(self, token: str) -> None:
        self._s = requests.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    # ── low-level helpers ──────────────────────────────────────────────────────
    def _get(self, path: str) -> dict:
        r = self._s.get(f"{MACHINES_API}{path}")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        r = self._s.post(f"{MACHINES_API}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = self._s.delete(f"{MACHINES_API}{path}")
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    # ── apps ───────────────────────────────────────────────────────────────────
    def app_exists(self, app_name: str) -> bool:
        try:
            self._get(f"/v1/apps/{app_name}")
            return True
        except requests.HTTPError as exc:
            if exc.response.status_code == 404:
                return False
            raise

    def create_app(self, app_name: str, org_slug: str) -> dict:
        return self._post("/v1/apps", {"name": app_name, "org_slug": org_slug})

    def delete_app(self, app_name: str) -> None:
        self._delete(f"/v1/apps/{app_name}")

    # ── IP addresses ───────────────────────────────────────────────────────────
    def list_ips(self, app_name: str) -> list[dict]:
        return self._get(f"/v1/apps/{app_name}/ip_assignments")["ips"]

    def allocate_ip(self, app_name: str, ip_type: str, region: str) -> dict:
        return self._post(f"/v1/apps/{app_name}/ip_assignments", {
            "type": ip_type,
            "region": region,
        })

    def release_ip(self, app_name: str, ip_address: str) -> None:
        self._delete(f"/v1/apps/{app_name}/ip_assignments/{ip_address}")

    # ── volumes ────────────────────────────────────────────────────────────────
    def list_volumes(self, app_name: str) -> list[dict]:
        return self._get(f"/v1/apps/{app_name}/volumes")

    def create_volume(
        self, app_name: str, name: str, region: str, size_gb: int
    ) -> dict:
        return self._post(
            f"/v1/apps/{app_name}/volumes",
            {"name": name, "region": region, "size_gb": size_gb, "encrypted": True},
        )

    def delete_volume(self, app_name: str, vol_id: str) -> None:
        self._delete(f"/v1/apps/{app_name}/volumes/{vol_id}")

    # ── machines ───────────────────────────────────────────────────────────────
    def list_machines(self, app_name: str) -> list[dict]:
        return self._get(f"/v1/apps/{app_name}/machines")

    def create_machine(self, app_name: str, config: dict) -> dict:
        return self._post(f"/v1/apps/{app_name}/machines", config)

    def delete_machine(self, app_name: str, machine_id: str) -> None:
        try:
            self._post(f"/v1/apps/{app_name}/machines/{machine_id}/stop", {
                "signal": "SIGTERM",
                "timeout": "5s",
            })
            time.sleep(6)
        except Exception:
            pass
        self._delete(f"/v1/apps/{app_name}/machines/{machine_id}?force=true")


# ── Machine config builder ─────────────────────────────────────────────────────
def build_machine_config(cfg: dict, volume_id: str) -> dict:
    """Translate infra_config.yml into the Machines API payload."""
    svc = cfg["http_service"]
    hc  = cfg["health_check"]
    return {
        "region": cfg["app"]["region"],
        "config": {
            "image": cfg["image"]["ref"],
            "env": cfg.get("env", {}),
            "metadata": {
                "fly_platform_version": "v2",
                "fly_process_group":    "app",
            },
            "guest": {
                "cpu_kind":  cfg["vm"]["cpu_kind"],
                "cpus":      cfg["vm"]["cpus"],
                "memory_mb": cfg["vm"]["memory_mb"],
            },
            "mounts": [{
                "volume":                   volume_id,
                "path":                     cfg["volume"]["mount_path"],
                "extend_threshold_percent": 80,
                "add_size_gb":              1,
                "size_gb_limit":            100,
            }],
            "services": [{
                "protocol":           "tcp",
                "internal_port":      svc["internal_port"],
                "autostop":           svc["auto_stop"],
                "autostart":          svc["auto_start"],
                "min_machines_running": svc["min_machines"],
                "ports": [
                    {"port": 80,  "handlers": ["http"], "force_https": svc["force_https"]},
                    {"port": 443, "handlers": ["tls", "http"]},
                ],
                "checks": [{
                    "type":         "http",
                    "port":         svc["internal_port"],
                    "path":         hc["path"],
                    "interval":     f"{hc['interval_ms'] // 1000}s",
                    "timeout":      f"{hc['timeout_ms'] // 1000}s",
                    "grace_period": f"{hc['grace_period_ms'] // 1000}s",
                }],
            }],
        },
    }


# ── plan ───────────────────────────────────────────────────────────────────────
def cmd_plan(cfg: dict, state: dict, client: FlyClient) -> None:
    app_name = cfg["app"]["name"]
    print(bold("\nNimbus IaC  ─  Plan\n"))

    rows: list[tuple[str, str, str]] = []

    # app
    exists = client.app_exists(app_name)
    rows.append((
        "  (no-op)" if exists else green("+ create"),
        "fly_app",
        app_name + (" (existing)" if exists else ""),
    ))

    # IPs
    if "fly_ip_shared_v4" not in state["resources"]:
        rows.append((green("+ create"), "fly_ip.shared_v4", "shared IPv4"))
    else:
        rows.append(("  (no-op)", "fly_ip.shared_v4", state["resources"]["fly_ip_shared_v4"].get("address", "")))

    if "fly_ip_v6" not in state["resources"]:
        rows.append((green("+ create"), "fly_ip.v6", "dedicated IPv6"))
    else:
        rows.append(("  (no-op)", "fly_ip.v6", state["resources"]["fly_ip_v6"].get("address", "")))

    # volume
    vol_key = f"fly_volume.{cfg['volume']['name']}"
    if vol_key not in state["resources"]:
        rows.append((green("+ create"), vol_key, f"{cfg['volume']['size_gb']} GB  region={cfg['app']['region']}"))
    else:
        rows.append(("  (no-op)", vol_key, state["resources"][vol_key].get("id", "")))

    # machine
    if "fly_machine.app" not in state["resources"]:
        rows.append((green("+ create"), "fly_machine.app", cfg["image"]["ref"]))
    else:
        rows.append(("  (no-op)", "fly_machine.app", state["resources"]["fly_machine.app"].get("id", "")))

    for action, resource, detail in rows:
        print(f"  {action}  {bold(resource)}  {cyan(detail)}")

    creates = sum(1 for a, _, _ in rows if "create" in a)
    print(f"\n{bold('Plan:')}  {green(str(creates) + ' to create')},  0 to destroy.\n")
    print(f"Run {bold('apply')} to execute.\n")


# ── apply ──────────────────────────────────────────────────────────────────────
def cmd_apply(cfg: dict, state: dict, client: FlyClient) -> None:
    app_name = cfg["app"]["name"]
    print(bold("\nNimbus IaC  ─  Apply\n"))

    # 1. App ───────────────────────────────────────────────────────────────────
    if not client.app_exists(app_name):
        print(f"  {green('+ fly_app')}  {app_name} …")
        client.create_app(app_name, cfg["app"]["org"])
        state["resources"]["fly_app"] = {"name": app_name, "status": "created"}
        save_state(state)
        print(f"    {green('✓')} App created.")
    else:
        state["resources"]["fly_app"] = {"name": app_name, "status": "existing"}
        print(f"  {yellow('~ fly_app')}  {app_name} already exists — skipping.")

    # 2. IP addresses ──────────────────────────────────────────────────────────
    existing_ips = client.list_ips(app_name)
    # REST response: {"ip": "1.2.3.4", "shared": true/false, "region": "..."}
    # derive type from "shared" field: shared=True → shared_v4, shared=False → v6
    def _ip_type(ip: dict) -> str:
        return "shared_v4" if ip.get("shared") else "v6"
    by_type: dict[str, dict] = {_ip_type(ip): ip for ip in existing_ips}

    for ip_type, state_key, label in [
        ("shared_v4", "fly_ip_shared_v4", "fly_ip.shared_v4"),
        ("v6",        "fly_ip_v6",        "fly_ip.v6"),
    ]:
        if ip_type not in by_type:
            print(f"  {green('+ ' + label)}  allocating …")
            ip = client.allocate_ip(app_name, ip_type, cfg["app"]["region"])
            state["resources"][state_key] = {"address": ip["ip"], "type": ip_type}
            save_state(state)
            print(f"    {green('✓')}  {ip['ip']}")
        else:
            ip = by_type[ip_type]
            state["resources"][state_key] = {"address": ip["ip"], "type": ip_type}
            print(f"  {yellow('~ ' + label)}  {ip['ip']} already allocated — skipping.")

    # 3. Volume ────────────────────────────────────────────────────────────────
    vol_cfg = cfg["volume"]
    vol_key = f"fly_volume.{vol_cfg['name']}"
    volumes = client.list_volumes(app_name)
    existing_vol = next((v for v in volumes if v["name"] == vol_cfg["name"]), None)

    if not existing_vol:
        print(f"  {green('+ ' + vol_key)}  {vol_cfg['name']} ({vol_cfg['size_gb']} GB) …")
        vol = client.create_volume(
            app_name, vol_cfg["name"], cfg["app"]["region"], vol_cfg["size_gb"]
        )
        state["resources"][vol_key] = {
            "id": vol["id"], "name": vol["name"], "region": vol.get("region", cfg["app"]["region"])
        }
        save_state(state)
        print(f"    {green('✓')}  Volume {vol['id']} created.")
    else:
        state["resources"][vol_key] = {
            "id": existing_vol["id"],
            "name": existing_vol["name"],
            "region": existing_vol.get("region", cfg["app"]["region"]),
        }
        print(f"  {yellow('~ ' + vol_key)}  {existing_vol['id']} already exists — skipping.")

    vol_id = state["resources"][vol_key]["id"]

    # 4. Machine ───────────────────────────────────────────────────────────────
    machines = client.list_machines(app_name)
    existing_machine = machines[0] if machines else None

    if not existing_machine:
        print(f"  {green('+ fly_machine.app')}  creating …")
        payload = build_machine_config(cfg, vol_id)
        machine = client.create_machine(app_name, payload)
        state["resources"]["fly_machine.app"] = {
            "id":         machine["id"],
            "state":      machine.get("state", "created"),
            "private_ip": machine.get("private_ip"),
        }
        print(f"    {green('✓')}  Machine {machine['id']} created.")
    else:
        state["resources"]["fly_machine.app"] = {
            "id":         existing_machine["id"],
            "state":      existing_machine.get("state", "unknown"),
            "private_ip": existing_machine.get("private_ip"),
        }
        print(f"  {yellow('~ fly_machine.app')}  {existing_machine['id']} already exists — skipping.")

    # Outputs ──────────────────────────────────────────────────────────────────
    state["outputs"] = {"app_url": f"https://{app_name}.fly.dev"}
    save_state(state)

    print(f"\n{bold('Apply complete.')}")
    print(f"  {bold('app_url')} = {cyan(state['outputs']['app_url'])}\n")


# ── destroy ────────────────────────────────────────────────────────────────────
def cmd_destroy(cfg: dict, state: dict, client: FlyClient) -> None:
    app_name = cfg["app"]["name"]
    print(bold(f"\n{red('Nimbus IaC  ─  Destroy')}\n"))
    print(red("  WARNING: This will permanently delete the machine, volume, IPs, and app."))
    confirm = input(f"\n  Type the app name to confirm [{bold(app_name)}]: ").strip()
    if confirm != app_name:
        print("\nAborted — no changes made.\n")
        return

    print()

    # Machine — must be fully deleted before volume can be freed
    if "fly_machine.app" in state["resources"]:
        mid = state["resources"]["fly_machine.app"]["id"]
        print(f"  {red('- fly_machine.app')}  {mid} …")
        client.delete_machine(app_name, mid)
        del state["resources"]["fly_machine.app"]
        save_state(state)
        print(f"    {green('✓')} Deleted.")

    # Volume
    vol_key = f"fly_volume.{cfg['volume']['name']}"
    if vol_key in state["resources"]:
        vid = state["resources"][vol_key]["id"]
        print(f"  {red('- ' + vol_key)}  {vid} …")
        client.delete_volume(app_name, vid)
        del state["resources"][vol_key]
        save_state(state)
        print(f"    {green('✓')} Deleted.")

    # IPs
    for state_key, label in [
        ("fly_ip_shared_v4", "fly_ip.shared_v4"),
        ("fly_ip_v6",        "fly_ip.v6"),
    ]:
        if state_key in state["resources"]:
            ip_address = state["resources"][state_key]["address"]
            print(f"  {red('- ' + label)}  {ip_address} …")
            client.release_ip(app_name, ip_address)
            del state["resources"][state_key]
            save_state(state)
            print(f"    {green('✓')} Released.")

    # App
    print(f"  {red('- fly_app')}  {app_name} …")
    client.delete_app(app_name)
    state["resources"] = {}
    state["outputs"]   = {}
    save_state(state)

    print(f"\n{bold(red('Destroy complete.'))}\n")


# ── show ───────────────────────────────────────────────────────────────────────
def cmd_show(state: dict) -> None:
    print(bold("\nNimbus IaC  ─  State\n"))
    print(json.dumps(state, indent=2))
    print()


# ── output ─────────────────────────────────────────────────────────────────────
def cmd_output(state: dict) -> None:
    print(bold("\nNimbus IaC  ─  Outputs\n"))
    for k, v in state.get("outputs", {}).items():
        print(f"  {bold(k)} = {cyan(v)}")
    res = state.get("resources", {})
    if "fly_machine.app" in res:
        m = res["fly_machine.app"]
        print(f"  {bold('machine_id')}    = {cyan(m.get('id', 'unknown'))}")
        print(f"  {bold('machine_state')} = {cyan(m.get('state', 'unknown'))}")
    if "fly_ip_shared_v4" in res:
        print(f"  {bold('ipv4')} = {cyan(res['fly_ip_shared_v4'].get('address', 'unknown'))}")
    if "fly_ip_v6" in res:
        print(f"  {bold('ipv6')} = {cyan(res['fly_ip_v6'].get('address', 'unknown'))}")
    print()


# ── refresh ────────────────────────────────────────────────────────────────────
def cmd_refresh(cfg: dict, state: dict, client: FlyClient) -> None:
    app_name = cfg["app"]["name"]
    print(bold("\nNimbus IaC  ─  Refresh\n"))

    if not client.app_exists(app_name):
        print(f"  {yellow('!')} App {app_name} not found on Fly.io — state cleared.")
        state["resources"] = {}
        state["outputs"]   = {}
        save_state(state)
        return

    state["resources"]["fly_app"] = {"name": app_name, "status": "existing"}

    # IPs — rebuild from scratch so stale entries are removed
    state["resources"].pop("fly_ip_shared_v4", None)
    state["resources"].pop("fly_ip_v6", None)
    for ip in client.list_ips(app_name):
        ip_type = "shared_v4" if ip.get("shared") else "v6"
        key = "fly_ip_shared_v4" if ip_type == "shared_v4" else "fly_ip_v6"
        state["resources"][key] = {"address": ip["ip"], "type": ip_type}
        print(f"  {green('↻')} {key}: {ip['ip']}")

    # Volumes — remove stale keys then repopulate
    for key in [k for k in state["resources"] if k.startswith("fly_volume.")]:
        del state["resources"][key]
    for vol in client.list_volumes(app_name):
        key = f"fly_volume.{vol['name']}"
        state["resources"][key] = {
            "id": vol["id"], "name": vol["name"], "region": vol.get("region")
        }
        print(f"  {green('↻')} {key}: {vol['id']}  ({vol.get('size_gb')} GB)")

    machines = client.list_machines(app_name)
    if machines:
        m = machines[0]
        state["resources"]["fly_machine.app"] = {
            "id": m["id"], "state": m.get("state"), "private_ip": m.get("private_ip")
        }
        print(f"  {green('↻')} fly_machine.app: {m['id']}  ({m.get('state')})")
    else:
        state["resources"].pop("fly_machine.app", None)
        print(f"  {yellow('!')} No machines found.")

    state["outputs"] = {"app_url": f"https://{app_name}.fly.dev"}
    save_state(state)
    print(f"\n{bold('Refresh complete.')}\n")


# ── entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nimbus_iac",
        description="Terraform-style Fly.io provisioner for Nimbus",
    )
    parser.add_argument(
        "command",
        choices=["plan", "apply", "destroy", "show", "output", "refresh"],
    )
    args = parser.parse_args()

    # show / output don't need a token
    token = os.environ.get("FLY_API_TOKEN", "")
    if not token and args.command not in ("show", "output"):
        sys.exit(
            f"{red('Error:')} FLY_API_TOKEN is not set.\n"
            "  Get a token: fly tokens create deploy -a ospsd-team-2\n"
            "  Then: export FLY_API_TOKEN=<token>"
        )

    cfg    = load_config()
    state  = load_state()
    client = FlyClient(token) if token else None  # type: ignore[arg-type]

    dispatch = {
        "plan":    lambda: cmd_plan(cfg, state, client),
        "apply":   lambda: cmd_apply(cfg, state, client),
        "destroy": lambda: cmd_destroy(cfg, state, client),
        "show":    lambda: cmd_show(state),
        "output":  lambda: cmd_output(state),
        "refresh": lambda: cmd_refresh(cfg, state, client),
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
