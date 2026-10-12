# OCI capture VM runbook (OCI-1)

Runs the existing R0 (BTCUSDT production contract v1) and R1B U2 (BTCUSDT, ETHUSDT,
SOLUSDT) public recorders unattended on one OCI Always Free Ubuntu VM. Public
market data only: no credential, account, private API, order or broker. The VM
runs `scripts/capture_bybit_public.py` unchanged: same contracts, source ids,
manifests, coverage rules and replay. It needs only the Python standard library
(`tests/test_oci_recorder_deployment.py` pins this), so nothing is pip-installed
and no dashboard, database or analytics code is installed.

| Unit | Command | Root (`/etc/trade-capture/capture.env`) |
| :--- | :--- | :--- |
| `trade-capture-r0` | `supervise --segment-seconds 3600` | `R0_ROOT` |
| `trade-capture-u2` | `universe --segment-seconds 3600` | `U2_ROOT` |
| `trade-capture-health.timer` | read-only snapshot at HH:10 UTC | both |

Restart semantics: `Restart=on-failure` restarts a crash, OOM kill or signal
death after 60 s as a **new session**, at most 10 times an hour. Exit 2 (disk
floor) and 4 (root already locked) are `RestartPreventExitStatus`: the unit
stays failed until an operator acts. `systemctl stop` sends SIGINT, which
finalizes every lane with `OPERATOR_INTERRUPT`; a kill after 180 s leaves the
open partition PARTIAL. Every hole between sessions (crash, reboot, upgrade) is a
derived gap, never bridged. The units refuse to start unless
`/srv/trade-capture` is a mounted volume, so capture can never fill the boot volume.

## 1. Owner prepares in the Oracle Console

1. **Home region.** Always Free capacity exists only in the tenancy's home region,
   fixed at sign-up. Bybit restricts some jurisdictions; choose a region whose
   egress reaches `stream.bybit.com` and `api.bybit.com` (check in step 3 with
   `capture_bybit_public.py clock` before enabling anything).
2. **Idle reclamation (decision).** Oracle may reclaim an idle Always Free
   instance: 7-day p95 CPU < 20 %, network < 20 % and, for A1, memory < 20 %. The
   recorders use ~3 % of one core and < 100 MiB, so the VM *will* look idle.
   Options: upgrade the tenancy to Pay As You Go (Always Free limits stay free;
   set a budget alert) or accept reclamation risk and rely on backups. Do not burn
   CPU to look busy.
3. **Instance.** Shape `VM.Standard.A1.Flex`, 1 OCPU / 6 GB (half the free
   2 OCPU / 12 GB A1 pool), image Canonical Ubuntu 24.04 (aarch64), public IPv4,
   your SSH public key. Fallback if A1 has no capacity: `VM.Standard.E2.1.Micro`
   (x86, 1 GB RAM) runs the same units. Boot volume ~50 GB.
4. **Archive block volume.** ~150 GB in the same availability domain, attachment
   type paravirtualized, device path `/dev/oracleoci/oraclevdb`. Boot plus block
   volumes must stay within the 200 GB Always Free total.
5. **Network.** VCN with an internet gateway; the security list allows ingress TCP
   22 **only from your own /32** (or use the free OCI Bastion service instead).
   Egress: default allow-all. The recorders need no inbound port.

Sizing evidence (`docs/current-phase.yaml` r1a_capacity_evidence, upper
envelope): U2 ~0.75 GB/day gzip-compacted, R0 ~0.30, together ~1.05 GB/day. A
150 GB volume less the 20 GiB floor is ~115 days, so OR-1 retention/offload still
has to be decided within that time. The health check measures the real rate.

## 2. Bootstrap the VM (once, as the `ubuntu` admin)

```bash
sudo apt-get update && sudo apt-get -y full-upgrade
sudo apt-get install -y chrony unattended-upgrades
echo 'Unattended-Upgrade::Automatic-Reboot "false";' | sudo tee /etc/apt/apt.conf.d/51no-auto-reboot
sudo mkfs.ext4 -L trade-capture /dev/oracleoci/oraclevdb      # new, empty volume only
sudo mkdir -p /srv/trade-capture
echo "UUID=$(sudo blkid -s UUID -o value /dev/oracleoci/oraclevdb) /srv/trade-capture ext4 defaults,noatime,_netdev,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount /srv/trade-capture && findmnt /srv/trade-capture
sudo timedatectl set-timezone UTC                              # display only; evidence is UTC nanos
```

SSH: keep the image defaults (key-only, `PasswordAuthentication no`,
`PermitRootLogin` not `yes`); confirm with `sudo sshd -T | grep -Ei 'passwordauth|permitroot'`.
Reboots are operator-chosen (outside an acceptance window), never automatic.

## 3. Install a release

On the dev machine, package exactly one commit (code only):

```bash
git archive --format=tar.gz -o capture-<commit>.tar.gz <commit> scripts/capture_bybit_public.py src/trade_platform deploy/oci
```

On the VM:

```bash
sudo install -d -m 0755 /opt/trade-capture/releases/<commit>
sudo tar -xzf capture-<commit>.tar.gz -C /opt/trade-capture/releases/<commit>
sudo /opt/trade-capture/releases/<commit>/deploy/oci/install.sh
sudo usermod -aG trade-capture ubuntu                           # read access for backups
sudo -u trade-capture python3 -I -B /opt/trade-capture/current/scripts/capture_bybit_public.py clock
chronyc tracking && timedatectl show -p NTPSynchronized
```

`install.sh` creates the `trade-capture` system user, `/srv/trade-capture`
(0750), `/etc/trade-capture/capture.env` from the example (validation roots,
never overwritten), the units, the bounded journald drop-in and the OCI NTP source
(`169.254.169.254`). It starts nothing. Clock: chrony may step the clock only in
its first three updates after boot, before `time-sync.target` lets the recorders
start; later corrections are slews, which move wall and monotonic clocks
together and never trip the recorder's 2 s discontinuity rule.

## 4. Validate beside Windows (validation roots, non-authoritative)

```bash
sudo systemctl enable --now trade-capture-r0.service trade-capture-u2.service trade-capture-health.timer
```

The validation roots record the same sources as the Windows recorders. They are
a trial only: never merged into, backed up into or reported as the archive, so
there is never a second authoritative writer. Validate for the owner's burn-in
(e.g. 24 jointly COMPLETE hours) and include one deliberate `sudo reboot` and
one `sudo systemctl kill -s SIGKILL trade-capture-u2` to see the honest gap and
the automatic new session.

## 5. Health, remotely

```bash
ssh <vm> sudo -u trade-capture /opt/trade-capture/current/deploy/oci/trade-capture-health.sh
ssh <vm> journalctl -u trade-capture-health -n 40 --no-pager
ssh <vm> systemctl --failed
ssh <vm> sudo -u trade-capture python3 -I -B /opt/trade-capture/current/scripts/capture_bybit_public.py --root /srv/trade-capture/validation/capture-universe-r1b universe-acceptance --max-unproven-seconds <S>
```

The health script exits 1 (`verdict ATTENTION`) if a unit is not active, a root's
lock is not held, the newest manifest is older than 75 min, free space is at or
below the floor, or the clock is not NTP-synchronized. Runway is computed from
the bytes actually written on the previous UTC day.

## 6. Upgrades

A restart ends the open hour, so the hour is not COMPLETE and an acceptance run
restarts: upgrade outside acceptance windows. Install the new release with step
3, then `sudo systemctl restart trade-capture-r0 trade-capture-u2`. Old releases
stay under `/opt/trade-capture/releases/`; roll back by re-running that release's
`install.sh` and restarting.

## 7. Backups

Pull finalized partitions to a second machine and re-verify them there (WSL on
the Windows host has `rsync`):

```bash
rsync -a --ignore-existing <vm>:/srv/trade-capture/archive/ ~/oci-pull/
python scripts/capture_bybit_public.py --root ~/oci-pull/capture backup <verified-destination>/capture
python scripts/capture_bybit_public.py --root ~/oci-pull/capture-universe-r1b backup <verified-destination>/capture-universe-r1b
```

`backup` copies only COMPLETE partitions and re-verifies every hash. Optional:
manual block-volume backups (five are included in Always Free).

## 8. Recovery

| Symptom | Action |
| :--- | :--- |
| Unit failed, `last_exit=2` | Disk floor. Free space on the archive volume (offload verified partitions, or grow the volume within the free total), then `systemctl start`. Never lower `MIN_FREE_GB` to get going. |
| Unit failed, `last_exit=4` | Another recorder holds that root. Find it (`status`); never run two. |
| Unit failed, start limit hit | Ten crashes in an hour: `journalctl -u <unit>`, fix, `systemctl reset-failed <unit>`, start. |
| Units not started after boot | `/srv/trade-capture` not mounted (the assert held). Fix the attachment/fstab. |
| VM or volume lost | New VM (steps 1-3); history comes back from the step 7 copies via `backup` into the new archive root. Re-provisioning never bridges the hole. |

## 9. Cutover from Windows (owner-authorized only)

1. Owner approves the validation evidence and a cutover hour `T` (UTC).
2. VM: `sudo systemctl stop trade-capture-r0 trade-capture-u2`; edit
   `capture.env` to the `archive/` roots. Validation partitions stay where they are.
3. Windows, shortly after `T` (after the HH:00 hand-off): stop both recorders. A
   hard stop leaves the open segment PARTIAL (excluded); that is honest.
4. VM: `sudo systemctl start trade-capture-r0 trade-capture-u2`. From here the VM
   is the only writer. The hand-off hour is not COMPLETE; nothing bridges it.
5. History: on Windows `capture_bybit_public.py --root <windows root> backup <staging>`
   for the production and universe roots, copy the staging tree to the VM, then on
   the VM `--root <staging> backup /srv/trade-capture/archive/<root>` (every
   partition re-verified on arrival). The Windows archives are never deleted.
6. Rollback: stop the VM units, then start the Windows recorders (new sessions,
   explicit gap). Exactly one writer at any time.
