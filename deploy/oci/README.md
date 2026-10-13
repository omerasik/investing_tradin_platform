# OCI capture VM runbook (OCI-1)

Runs the existing R0 (BTCUSDT production contract v1) and R1B U2 (BTCUSDT, ETHUSDT,
SOLUSDT) public recorders unattended on an OCI VM: Oracle Linux 9 (SELinux
enforcing) or Ubuntu 24.04, aarch64 or x86_64. Public market data only: no
credential, account, private API, order or broker. The VM runs
`scripts/capture_bybit_public.py` unchanged -- same contracts, source ids,
manifests, coverage rules and replay. It needs only the Python standard library
(>= 3.11; `tests/test_oci_recorder_deployment.py` pins this), so nothing is
pip-installed and no dashboard, database or analytics code is installed.

| Unit | Command | Root (`/etc/trade-capture/capture.env`) |
| :--- | :--- | :--- |
| `trade-capture-r0` | `supervise --segment-seconds 3600` | `R0_ROOT` |
| `trade-capture-u2` | `universe --segment-seconds 3600` | `U2_ROOT` |
| `trade-capture-health.timer` | read-only snapshot at HH:10 UTC | both |

**Restart semantics.** `Restart=on-failure` restarts a crash, OOM kill or signal
death after 60 s as a **new session**, at most 10 times an hour. Exit 2 (disk
floor) and 4 (root already locked) are `RestartPreventExitStatus`: the unit stays
failed until an operator acts. `systemctl stop` sends SIGINT, which finalizes
every lane with `OPERATOR_INTERRUPT`; a kill after 180 s leaves the open
partition PARTIAL. Every hole between sessions -- crash, reboot, upgrade, host
migration -- is a derived gap; nothing bridges it and no coverage is claimed
across it. The units refuse to start unless `/srv/trade-capture` is a mounted
volume, so capture can never fill the boot volume.

**Interpreter.** `install.sh` links `/opt/trade-capture/python` to the first
Python >= 3.11 it finds (`TRADE_CAPTURE_PYTHON`, then `python3.12`, `3.11`,
`3.13`, `python3`). On Oracle Linux 9 that is `python3.12` from `ol9_appstream`
beside the untouched system `python3` (3.9).

## 1. Facts to check in the Oracle Console (owner)

From the Always Free resources page (docs.oracle.com, Free Tier, checked 2026-10):

* A1: "the first 1,500 OCPU hours and 9,000 GB hours per month" for
  `VM.Standard.A1.Flex`, "equivalent to 2 OCPUs and 12 GB of memory" for Always
  Free tenancies. After an upgrade Oracle "will only charge you for resource usage
  above the Always Free limits". The page does not say what happens when an Always
  Free tenancy exceeds them.
* Block Volume: 200 GB total, boot volumes and block volumes combined, home
  region only; five volume backups.
* Idle reclamation (Always Free instances): 7-day p95 CPU < 20 %, network < 20 %
  and, for A1, memory < 20 %.

Check, do not estimate: Billing & Cost Management > Cost Analysis (any A1 or
storage charge), Governance > Limits (A1 cores and memory), Block Storage (sum of
every boot and block volume in the tenancy, including stopped or other
instances). This runbook never resizes an instance or changes billing.

## 2. Host prerequisites (owner-approved changes to the VM)

1. **Archive volume.** A dedicated block volume, attached paravirtualized, read/write,
   sized so every volume in the tenancy stays within what the owner accepts (the
   200 GB Always Free total, if that is the policy). Check the device first --
   never format a disk that has a filesystem or partitions:
   ```bash
   ls -l /dev/oracleoci/ && lsblk -f
   sudo wipefs -n /dev/oracleoci/oraclevdb    # must print nothing
   ```
   Then `mkfs.xfs` (Oracle Linux) or `mkfs.ext4` (Ubuntu), an fstab entry
   `UUID=<uuid> /srv/trade-capture <fs> defaults,noatime,_netdev,nofail 0 0`, and
   `findmnt /srv/trade-capture`.
2. **Python >= 3.11.** Oracle Linux 9: `sudo dnf install -y python3.12`. Ubuntu
   24.04: already present.
3. **Clock.** chrony synced to the OCI NTP service `169.254.169.254` (Oracle
   Linux images ship it in `/etc/chrony.conf`; `install.sh` adds it on Ubuntu if
   missing). chrony may step the clock only in its first three updates after boot
   (`makestep 1 3`), before `time-sync.target` lets the recorders start
   (`install.sh` enables `chrony-wait.service`). Later corrections are slews, which
   move the wall and monotonic clocks together and never trip the recorder's 2 s
   discontinuity rule.
4. **Firewall.** No change: the recorders need outbound HTTPS/WSS only and no
   inbound port. SSH ingress stays restricted to the owner's address.

Optional and host-wide (affects every service's logs, so it is not installed by
default): `deploy/oci/journald-trade-capture.conf` into
`/etc/systemd/journald.conf.d/`, applied at the next journald restart.

## 3. Install a release

On the dev machine, package exactly one `main` commit (code only):

```bash
git archive --format=tar.gz -o capture-<commit>.tar.gz <commit> scripts/capture_bybit_public.py src/trade_platform deploy/oci
```

On the VM (`opc` on Oracle Linux, `ubuntu` on Ubuntu):

```bash
sudo install -d -m 0755 /opt/trade-capture/releases/<commit>
sudo tar -xzf /tmp/capture-<commit>.tar.gz -C /opt/trade-capture/releases/<commit>
sudo /opt/trade-capture/releases/<commit>/deploy/oci/install.sh
sudo usermod -aG trade-capture "$USER"                         # read access for backups
sudo -u trade-capture /opt/trade-capture/python -I -B /opt/trade-capture/current/scripts/capture_bybit_public.py clock
```

`install.sh` creates the `trade-capture` system user, `/srv/trade-capture`
(0750), `/etc/trade-capture/capture.env` from the example (validation roots,
never overwritten), the interpreter link and the units, enables the clock wait
and restores SELinux labels. It installs no package and starts nothing.

## 4. Stage 1: validation beside Windows (non-authoritative)

```bash
sudo systemctl enable --now trade-capture-r0.service trade-capture-u2.service trade-capture-health.timer
```

The validation roots record the same sources as the Windows recorders but are a
trial only: never merged into, backed up into or reported as the archive, so
there is never a second authoritative writer. Include one deliberate
`sudo systemctl kill -s SIGKILL trade-capture-u2` (expect a restart and an honest
gap) and, if the owner allows, one reboot.

### Acceptance criteria (proposed; the owner approves the numbers)

On a shared host a CPU weight raises the recorders' share under contention; it
does not bound how late they wake up. The cloud stage is accepted only on
measured evidence over the whole validation window:

| Criterion | Measure | Proposed bound |
| :--- | :--- | :--- |
| Clock sync | `chronyc tracking` System time; recorders' own `CLOCK_OFFSET_SAMPLE` (health probe) | synced; \|offset\| + bound <= 250 ms (the CLI `clock --warn-ms` default) in every hourly snapshot |
| Scheduling delay | health probe `sched_late`, measured in the recorders' CPUWeight class | p99 <= 10 ms and max <= 100 ms in every hourly snapshot |
| Continuity | `universe-acceptance --max-unproven-seconds S` (and `--verify` once on main) on the validation U2 root | 24 consecutive jointly COMPLETE hours; max unproven not above the Windows evidence (8.05 s); no lane restart except the deliberate test |
| Arrival timing | same-hour T4 dry run (`first_party_t4.py --source <SYMBOL>`) on cloud and Windows partitions | REAL_DATA + T4 professional; knowledge-minus-event upper bounds not worse than the Windows partitions of the same hours |
| Headroom | health probe `headroom`; health `runway` | MemAvailable >= 2 GiB; runway >= 30 days above the floor at the measured daily rate |

Any failed criterion means the shared host is not suitable as is (see 9).

## 5. Health, remotely

```bash
ssh <vm> sudo systemctl start trade-capture-health.service
ssh <vm> journalctl -u trade-capture-health -n 40 --no-pager
ssh <vm> systemctl --failed
ssh <vm> sudo -u trade-capture /opt/trade-capture/python -I -B /opt/trade-capture/current/scripts/capture_bybit_public.py --root /srv/trade-capture/validation/capture-universe-r1b universe-acceptance --max-unproven-seconds <S>
```

The health check exits 1 (`verdict ATTENTION`) if a unit is not active, a root's
lock is not held, the newest manifest is older than 75 min, free space is at or
below the floor, the clock is not NTP-synchronized, or a probe criterion fails.
Run it through the unit: an SSH session's cgroup does not carry the recorders'
CPU weight.

## 6. Upgrades

A restart ends the open hour, so that hour is not COMPLETE and an acceptance run
restarts: upgrade outside acceptance windows. Install the new release with step 3,
then `sudo systemctl restart trade-capture-r0 trade-capture-u2`. Old releases stay
under `/opt/trade-capture/releases/`; roll back by re-running that release's
`install.sh` and restarting.

## 7. Backups

Pull finalized partitions to a second machine and re-verify them there:

```bash
rsync -a --ignore-existing <vm>:/srv/trade-capture/archive/ ~/oci-pull/     # needs rsync on both ends
python scripts/capture_bybit_public.py --root ~/oci-pull/capture backup <verified-destination>/capture
python scripts/capture_bybit_public.py --root ~/oci-pull/capture-universe-r1b backup <verified-destination>/capture-universe-r1b
```

Without rsync on the VM, `ssh <vm> tar -C /srv/trade-capture/archive -cf - . | tar -xf -`
into an empty directory works the same way. `backup` copies only COMPLETE
partitions and re-verifies every hash.

## 8. Recovery and rollback

| Symptom | Action |
| :--- | :--- |
| Unit failed, `last_exit=2` | Disk floor. Free space on the archive volume (offload verified partitions, or grow the volume), then `systemctl start`. Never lower `MIN_FREE_GB` to get going. |
| Unit failed, `last_exit=4` | Another recorder holds that root. Find it (`status`); never run two. |
| Unit failed, start limit hit | Ten crashes in an hour: `journalctl -u <unit>`, fix, `systemctl reset-failed <unit>`, start. |
| Units not started after boot | `/srv/trade-capture` not mounted (the assert held). Fix the attachment/fstab. |
| SELinux denial | `sudo ausearch -m avc -ts recent`; re-run `install.sh` (restores labels). |
| Remove the deployment | `systemctl disable --now trade-capture-r0 trade-capture-u2 trade-capture-health.timer`; the archive volume and its data stay. |
| VM or volume lost | Rebuild (2-3); history comes back from the step 7 copies via `backup`. Re-provisioning never bridges the hole. |

## 9. Shared host or isolated recorder host

The recorders need ~3 % of one core and < 100 MiB, but on a host whose other
workloads keep every core busy they wait in the run queue, and arrival time is the
T4 evidence. If any stage-1 criterion fails on the shared host, isolate the
recorders: a host (or instance) whose CPUs are not saturated, chosen by the owner.
This runbook never stops, throttles or reconfigures other workloads.

## 10. Stage 2: cutover from Windows (owner-authorized only)

1. Owner approves the stage-1 evidence, the backups and a cutover hour `T` (UTC).
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
