#!/usr/bin/env python3
"""Create the DNS data model in NetBox and import DNS servers from a CSV.

Idempotent: safe to run again after editing the CSV (creates missing objects,
updates changed fields, never deletes anything).

Usage:
    export NETBOX_URL=http://127.0.0.1:8000  NETBOX_TOKEN=<api token>
    python3 netbox_bootstrap.py servers.csv            # dry run, validates CSV only
    python3 netbox_bootstrap.py servers.csv --apply    # writes to NetBox

CSV columns: name,site,mgmt_ip,platform   (mgmt_ip with prefix length, e.g. 10.10.0.11/24)
"""
import argparse
import csv
import ipaddress
import os
import re
import sys

PLATFORMS = {"unbound": "Unbound", "bind9": "BIND9"}
ROLE = {"name": "DNS Resolver", "slug": "dns-resolver", "color": "2196f3"}
MANUFACTURER = {"name": "Generic", "slug": "generic"}
DEVICE_TYPE = {"model": "Linux Server", "slug": "linux-server"}
TAGS = [
    # Prefixes carrying this tag are allowed to use the resolvers (access-control).
    {"name": "dns-client", "slug": "dns-client", "color": "4caf50"},
]
RPKI_CHOICES = {
    "name": "RPKI status",
    "extra_choices": [["valid", "Valid"], ["invalid", "Invalid"], ["not-found", "Not found"], ["unknown", "Unknown"]],
}
CUSTOM_FIELDS = [
    {"name": "origin_asn", "label": "Origin ASN", "type": "integer", "object_types": ["ipam.prefix"]},
    {"name": "asn_name", "label": "ASN name", "type": "text", "object_types": ["ipam.prefix"]},
    {"name": "rpki_status", "label": "RPKI status", "type": "select", "object_types": ["ipam.prefix"]},
    {"name": "enriched_at", "label": "Enriched at", "type": "datetime", "object_types": ["ipam.prefix"]},
    {"name": "os_version", "label": "OS version", "type": "text", "object_types": ["dcim.device"]},
    {"name": "dns_engine_version", "label": "DNS engine version", "type": "text", "object_types": ["dcim.device"]},
]
MGMT_IFACE = "mgmt0"


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def read_servers(path):
    """Validate the CSV fully before anything touches NetBox."""
    errors, rows, seen = [], [], set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"name", "site", "mgmt_ip", "platform"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for line, row in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in row.items()}
            name, site, ip, platform = row["name"], row["site"], row["mgmt_ip"], row["platform"].lower()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", name):
                errors.append(f"line {line}: bad name {name!r}")
            elif name in seen:
                errors.append(f"line {line}: duplicate name {name!r}")
            seen.add(name)
            if not site:
                errors.append(f"line {line}: site is empty")
            if platform not in PLATFORMS:
                errors.append(f"line {line}: platform must be one of {sorted(PLATFORMS)}")
            try:
                iface = ipaddress.ip_interface(ip)
                if "/" not in ip or iface.version != 4:
                    raise ValueError
            except ValueError:
                errors.append(f"line {line}: mgmt_ip must be IPv4 with prefix length, got {ip!r}")
            rows.append({"name": name, "site": site, "mgmt_ip": ip, "platform": platform})
    if errors:
        raise SystemExit("CSV errors:\n  " + "\n  ".join(errors))
    if not rows:
        raise SystemExit("CSV has no servers")
    return rows


def ensure(endpoint, lookup, data, log):
    """Get by lookup; create if missing, else update fields that differ."""
    obj = endpoint.get(**lookup)
    if obj is None:
        obj = endpoint.create({**lookup, **data})
        log.append(f"created {endpoint.name} {lookup}")
    elif obj.update(data):
        log.append(f"updated {endpoint.name} {lookup}")
    return obj


def bootstrap(nb, servers):
    log = []
    choice_set = ensure(nb.extras.custom_field_choice_sets, {"name": RPKI_CHOICES["name"]},
                        {"extra_choices": RPKI_CHOICES["extra_choices"]}, log)
    for cf in CUSTOM_FIELDS:
        data = {k: v for k, v in cf.items() if k != "name"}
        if cf["type"] == "select":
            data["choice_set"] = choice_set.id
        ensure(nb.extras.custom_fields, {"name": cf["name"]}, data, log)
    for tag in TAGS:
        ensure(nb.extras.tags, {"slug": tag["slug"]}, {"name": tag["name"], "color": tag["color"]}, log)

    role = ensure(nb.dcim.device_roles, {"slug": ROLE["slug"]}, {"name": ROLE["name"], "color": ROLE["color"]}, log)
    mfr = ensure(nb.dcim.manufacturers, {"slug": MANUFACTURER["slug"]}, {"name": MANUFACTURER["name"]}, log)
    dtype = ensure(nb.dcim.device_types, {"slug": DEVICE_TYPE["slug"]},
                   {"model": DEVICE_TYPE["model"], "manufacturer": mfr.id}, log)
    platforms = {p: ensure(nb.dcim.platforms, {"slug": p}, {"name": label}, log) for p, label in PLATFORMS.items()}

    for srv in servers:
        site = ensure(nb.dcim.sites, {"slug": slugify(srv["site"])}, {"name": srv["site"], "status": "active"}, log)
        dev = ensure(nb.dcim.devices, {"name": srv["name"]}, {
            "role": role.id, "device_type": dtype.id, "site": site.id,
            "platform": platforms[srv["platform"]].id, "status": "active",
        }, log)
        iface = ensure(nb.dcim.interfaces, {"device_id": dev.id, "name": MGMT_IFACE},
                       {"device": dev.id, "type": "virtual", "mgmt_only": True}, log)
        ip = nb.ipam.ip_addresses.get(address=srv["mgmt_ip"])
        if ip is None:
            ip = nb.ipam.ip_addresses.create({"address": srv["mgmt_ip"], "status": "active",
                                              "assigned_object_type": "dcim.interface",
                                              "assigned_object_id": iface.id})
            log.append(f"created ip {srv['mgmt_ip']}")
        elif getattr(ip, "assigned_object_id", None) not in (None, iface.id):
            # Never silently steal an IP that belongs to another device.
            raise SystemExit(f"{srv['mgmt_ip']} already assigned to another interface in NetBox; fix it by hand")
        elif ip.update({"assigned_object_type": "dcim.interface", "assigned_object_id": iface.id}):
            log.append(f"assigned ip {srv['mgmt_ip']}")
        if dev.update({"primary_ip4": ip.id}):
            log.append(f"set primary ip {dev.name} -> {srv['mgmt_ip']}")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--apply", action="store_true", help="write to NetBox (default: validate only)")
    args = ap.parse_args()

    servers = read_servers(args.csv)
    print(f"CSV OK: {len(servers)} servers, sites: {sorted({s['site'] for s in servers})}")
    if not args.apply:
        print("Dry run. Re-run with --apply to write to NetBox.")
        return

    import pynetbox  # imported here so validation works without it
    url, token = os.environ.get("NETBOX_URL"), os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        raise SystemExit("set NETBOX_URL and NETBOX_TOKEN")
    for line in bootstrap(pynetbox.api(url, token=token), servers) or ["nothing to change"]:
        print(line)


if __name__ == "__main__":
    sys.exit(main())
