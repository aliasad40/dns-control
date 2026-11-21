# DNS Control

Button-driven automation for the Unbound DNS resolvers. **Semaphore UI** gives a simple web GUI with
one button per task, plus run history and an encrypted key store. **Ansible playbooks** in this repo do
the work.

```
NOC IPs --443--> ufw --> nginx (host) --> 127.0.0.1:3000 Semaphore (docker) --SSH--> DNS servers
                                              |-- Postgres (docker, not published)
                                              '-- pulls playbooks from this Git repo (deploy key)
```
Web apps bind to loopback only, because Docker-published ports bypass ufw. nginx is a host process,
so ufw's 443 allowlist applies to it.

## Status
| Milestone | What | State |
|---|---|---|
| M1 | Repo cleanup (NetBox removed, static inventory) | done |
| M2 | Semaphore + Postgres + nginx 443 | done |
| M3 | Semaphore project, repo, inventory, key store, pinned host keys | planned |
| M4 | `dnsadmin` key-only user on every server (dns-lhr-01 first) | planned |
| M5 | Health check + config backup (read-only) | planned |
| M6 | Rolling reload + access-control prefixes (checkconf, backup, one server at a time) | planned |
| M7 | Hardening: no root password login, SSH only from mgmt VM / NOC | planned |

## Layout
```
semaphore/
  docker-compose.yml          # Semaphore v2.20.24 + Postgres 18, 127.0.0.1:3000 only
  .env.example                # copy to .env (git-ignored, chmod 600)
  nginx-semaphore.conf        # 443 -> 127.0.0.1:3000, incl. WebSocket for live task output
ansible/
  ansible.cfg                 # remote_user dnsadmin, host key checking ON
  inventory/hosts.example.yml # copy to hosts.yml (git-ignored) for CLI runs
  playbooks/
    bootstrap_dnsadmin.yml    # one-time: create dnsadmin with key-only login + sudo
    discover.yml              # read-only: versions, checkconf, copies of *.conf
```

## Semaphore deploy (mgmt VM)
```bash
cd semaphore && cp .env.example .env && chmod 600 .env    # fill with random values (see file)
sudo docker compose up -d --wait
sudo install -m 0644 nginx-semaphore.conf /etc/nginx/sites-available/semaphore.conf   # edit server_name
sudo ln -s /etc/nginx/sites-available/semaphore.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```
After the first start, remove `ADMIN_PASSWORD` from `.env` and run `sudo docker compose up -d` again.
**Back up `.env` offline.** Without `ACCESS_KEY_ENCRYPTION`, every password and key in the Key Store
is unreadable.
Verify: `curl -sk https://<mgmt-ip>/api/ping` returns `pong`; `ss -tlnp` shows 3000 only on 127.0.0.1.
Rollback: `sudo rm /etc/nginx/sites-enabled/semaphore.conf && sudo systemctl reload nginx && sudo docker compose down`.

## Running from the CLI (mgmt VM)
```bash
python3 -m venv ~/dnsctl && ~/dnsctl/bin/pip install ansible-core
cd ansible && cp inventory/hosts.example.yml inventory/hosts.yml   # put real IPs in
~/dnsctl/bin/ansible-inventory --graph
```
The normal way to run tasks is the Semaphore GUI (from M3 on).

## Mgmt VM base (already applied)
- ufw: default deny incoming; 22/tcp and 443/tcp only from the NOC IPs.
- sshd drop-in `/etc/ssh/sshd_config.d/00-hardening.conf`: `PermitRootLogin no`, `MaxAuthTries 3`,
  `LoginGraceTime 30`, `X11Forwarding no`. It sorts before `50-cloud-init.conf`, and sshd uses the
  first value it reads.
- nginx with a self-signed IP-SAN certificate `/etc/nginx/ssl/dns-control.{crt,key}` (replace it when a domain is added).
