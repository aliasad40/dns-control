"""Self-test with a fake NetBox: CSV validation + idempotent bootstrap. Run: python test_netbox_bootstrap.py"""
import os
import tempfile
from types import SimpleNamespace

import netbox_bootstrap as nbb


class FakeRecord(SimpleNamespace):
    def update(self, data):
        changed = {k: v for k, v in data.items() if getattr(self, k, None) != v}
        self.__dict__.update(changed)
        return bool(changed)


class FakeEndpoint:
    def __init__(self, name):
        self.name, self.rows = name, []

    def get(self, **kw):
        hits = [r for r in self.rows if all(getattr(r, k, None) == v for k, v in kw.items())]
        assert len(hits) <= 1, f"duplicate in {self.name}: {kw}"
        return hits[0] if hits else None

    def create(self, data):
        rec = FakeRecord(id=len(self.rows) + 1, **data)
        if self.name == "interfaces":
            rec.device_id = data["device"]
        self.rows.append(rec)
        return rec


class FakeNB:
    def __init__(self):
        mk = lambda *names: SimpleNamespace(**{n: FakeEndpoint(n) for n in names})
        self.extras = mk("custom_field_choice_sets", "custom_fields", "tags")
        self.dcim = mk("device_roles", "manufacturers", "device_types", "platforms", "sites", "devices", "interfaces")
        self.ipam = mk("ip_addresses")


def write_csv(text):
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(text)
    fh.close()
    return fh.name


def expect_exit(text, needle):
    path = write_csv(text)
    try:
        nbb.read_servers(path)
    except SystemExit as e:
        assert needle in str(e), str(e)
    else:
        raise AssertionError(f"expected failure containing {needle!r}")
    finally:
        os.unlink(path)


here = os.path.dirname(os.path.abspath(__file__))
servers = nbb.read_servers(os.path.join(here, "servers.example.csv"))
assert len(servers) == 3

hdr = "name,site,mgmt_ip,platform\n"
expect_exit(hdr + "a,Lahore,10.0.0.1,unbound\n", "prefix length")
expect_exit(hdr + "a,Lahore,10.0.0.1/24,powerdns\n", "platform")
expect_exit(hdr + "a,Lahore,10.0.0.1/24,unbound\na,Lahore,10.0.0.2/24,unbound\n", "duplicate")
expect_exit(hdr + "a,,10.0.0.1/24,unbound\n", "site is empty")
expect_exit("name,site\n", "missing columns")

nb = FakeNB()
first = nbb.bootstrap(nb, servers)
assert len(nb.dcim.devices.rows) == 3 and len(nb.dcim.sites.rows) == 2
assert all(d.primary_ip4 for d in nb.dcim.devices.rows)
second = nbb.bootstrap(nb, servers)
assert second == [], f"second run should change nothing, got {second}"
assert len(nb.ipam.ip_addresses.rows) == 3

# An IP already owned by another interface must stop the run, not be stolen.
nb.ipam.ip_addresses.rows[0].assigned_object_id = 999
expect_steal = False
try:
    nbb.bootstrap(nb, servers)
except SystemExit as e:
    expect_steal = "already assigned" in str(e)
assert expect_steal

print(f"OK: first run {len(first)} changes, second run 0 changes, validation + IP-steal guard pass")
