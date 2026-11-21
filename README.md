# DNS Control — Milestone 0 (Foundation)

Goal: NetBox running on the mgmt VM, all DNS servers in NetBox, `dnsadmin` key access on each,
and a **read-only** discovery report. Nothing on the DNS servers' Unbound config changes in M0.

Run everything below **on the mgmt VM** (Ubuntu 26.04).

## 1. NetBox (docker) behind nginx
Layout: `NOC IPs --443--> ufw --> nginx (host) --> 127.0.0.1:8000 --> NetBox (docker)`.
NetBox listens on loopback only, because Docker-published ports bypass ufw. nginx is a host
process, so ufw's 443 allowlist applies to it.

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git python3-venv nginx
git clone -b release https://github.com/netbox-community/netbox-docker.git ~/netbox-docker
cp netbox/docker-compose.override.yml ~/netbox-docker/     # binds 127.0.0.1:8000
cd ~/netbox-docker && chmod 600 env/*.env
# Replace EVERY default secret in env/*.env with random values (DB/Redis passwords must match
# across files; SECRET_KEY and API_TOKEN_PEPPER_1 >= 50 chars). Also set in env/netbox.env:
#   CORS_ORIGIN_ALLOW_ALL=False
#   ALLOWED_HOSTS=<mgmt-ip-or-fqdn> localhost 127.0.0.1
#   CSRF_TRUSTED_ORIGINS=https://<mgmt-ip-or-fqdn>
sudo docker compose pull && sudo docker compose up -d   # first start runs migrations (~15 min);
                                                        # an early "unhealthy" is normal, check logs
sudo docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser
```

nginx with TLS (self-signed for an IP; use a real cert once a domain exists):
```bash
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
  -keyout /etc/nginx/ssl/netbox.key -out /etc/nginx/ssl/netbox.crt \
  -subj "/CN=<mgmt-ip>" -addext "subjectAltName=IP:<mgmt-ip>" -addext "extendedKeyUsage=serverAuth"
sudo chmod 600 /etc/nginx/ssl/netbox.key
sudo install -m 0644 netbox/nginx-netbox.conf /etc/nginx/sites-available/netbox.conf   # edit server_name
sudo ln -s /etc/nginx/sites-available/netbox.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx       # restart (not reload) to drop port 80
```
Open `https://<mgmt-ip>`, log in, create an API token (Admin → API Tokens).
Verify: `sudo docker compose ps` → all `healthy`; `ss -tlnp` → 8000 only on 127.0.0.1, no :80.
Rollback (web): `sudo rm /etc/nginx/sites-enabled/netbox.conf && sudo systemctl reload nginx`.

## 2. Tools on the mgmt VM
```bash
python3 -m venv ~/dnsctl && . ~/dnsctl/bin/activate
pip install ansible-core pynetbox pytz packaging
cd <this repo>/ansible && ansible-galaxy collection install -r requirements.yml
export NETBOX_URL=http://127.0.0.1:8000 NETBOX_API=$NETBOX_URL NETBOX_TOKEN=<token>
```

## 3. Load servers into NetBox
```bash
cd <this repo>/bootstrap
cp servers.example.csv servers.csv     # fill all 24 servers: name,site,mgmt_ip,platform
python3 netbox_bootstrap.py servers.csv          # validates only
python3 netbox_bootstrap.py servers.csv --apply  # writes to NetBox (safe to re-run)
```
Verify: `cd ../ansible && ansible-inventory --graph` → groups `sites_*` and `platforms_unbound` list all servers.

## 4. dnsadmin user (one server first!)
```bash
ssh-keygen -t ed25519 -f ~/.ssh/dnsadmin_ed25519 -C dnsadmin@mgmt
ssh-keyscan -t ed25519 <each-dns-ip> >> ~/.ssh/known_hosts   # compare fingerprints with the servers
ansible-playbook playbooks/bootstrap_dnsadmin.yml -u <your-admin-user> -k -K --limit dns-lhr-01
ansible-playbook playbooks/bootstrap_dnsadmin.yml -u <your-admin-user> -k -K   # then the rest
```
`-k` needs `sudo apt install sshpass`. Verify: `ansible all -m ping` → all `SUCCESS`.
Rollback (per server): `sudo userdel -r dnsadmin && sudo rm /etc/sudoers.d/dnsadmin`.

## 5. Read-only discovery
```bash
ansible-playbook playbooks/discover.yml
```
Output: `discovery/report.md` + `discovery/<host>/etc/unbound/*.conf`. Share both for milestone M1.
