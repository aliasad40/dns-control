# DNS Control — Milestone 0 (Foundation)

Goal: NetBox running on the mgmt VM, all DNS servers in NetBox, `dnsadmin` key access on each,
and a **read-only** discovery report. Nothing on the DNS servers' Unbound config changes in M0.

Run everything below **on the mgmt VM** (Ubuntu 24.04).

## 1. NetBox (docker)
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git python3-venv
sudo usermod -aG docker $USER        # log out/in after this
git clone -b release https://github.com/netbox-community/netbox-docker.git ~/netbox-docker
cp netbox/docker-compose.override.yml ~/netbox-docker/
cd ~/netbox-docker
echo "MGMT_IP=<mgmt-vm-ip>" > .env
# Replace EVERY default password/secret in env/*.env (DB_PASSWORD, SECRET_KEY, REDIS_PASSWORD...)
docker compose pull && docker compose up -d
docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser
```
Open `http://<mgmt-vm-ip>:8000`, log in, create an API token (Admin → API Tokens).
Verify: `docker compose ps` → all services `healthy`.

## 2. Tools on the mgmt VM
```bash
python3 -m venv ~/dnsctl && . ~/dnsctl/bin/activate
pip install ansible-core pynetbox pytz packaging
cd <this repo>/ansible && ansible-galaxy collection install -r requirements.yml
export NETBOX_URL=http://<mgmt-vm-ip>:8000 NETBOX_API=$NETBOX_URL NETBOX_TOKEN=<token>
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
