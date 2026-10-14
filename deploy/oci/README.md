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
| `trade-capture-health.timer` | read-only snapshot at HH:10 UTC (+ dead-man ping, 7c) | both |
| `trade-capture-backup.timer` | off-host upload at HH:25 UTC, owner-enabled (7a) | both, read-only |
| `trade-capture-alert@.service` | `/fail` ping when a unit fails (7c) | none |

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

The health check exits 1 (`service ATTENTION`) if a unit is not active, a root's
lock is not held, the newest manifest is older than 75 min, free space is at or
below the floor, the clock is not NTP-synchronized, or a probe criterion fails.
Run it through the unit: an SSH session's cgroup does not carry the recorders'
CPU weight.

`service OK` is service health, never market-data evidence: on 2026-10-14 it
read OK every hour while connection losses and > 10 s hand-offs broke the
window. The `u2_hours` lines (`universe-hours --hours 2`) state, per symbol and
whole UTC hour, whether one proven run reached the hour's end, how much of the
hour it left unproven, and every stop inside the hour (`CONNECTION_LOST`,
`PEER_CLOSED`, ...) -- from COMPLETE partitions only, with no bound and no
verdict, and without changing the exit code. COMPLETE hours are decided only by
`universe-acceptance --max-unproven-seconds S`.

The health unit does not run `chronyc`: under `NoNewPrivileges` SELinux denies
its `chronyc_t` transition (`nnp_transition`). Sync state comes from
`timedatectl`, the offset from the recorders' own clock samples; run
`chronyc tracking` from an SSH session for chrony's view.

## 6. Upgrades

A restart ends the open hour, so that hour is not COMPLETE and an acceptance run
restarts: upgrade outside acceptance windows. Install the new release with step 3
(running recorders keep the release they resolved at start), then, between
HH:50 and HH:58 UTC, `sudo systemctl restart trade-capture-r0 trade-capture-u2`:
only hour HH is lost, and the new session's first bounded stop is the HH+1:00
hand-off. Old releases stay under `/opt/trade-capture/releases/`; roll back by
re-running that release's `install.sh` and restarting the same way.

Since the hourly hand-off fix the next segment starts as soon as the last one is
finalized; the finished hour is compacted by the new segment once all its lanes
have acknowledged their subscriptions (one partition per minute, never within
2 min of the next hand-off). Raw partitions of the last hour therefore remain for
a few minutes after HH:00; `compact` and `backup` treat them exactly as before.

## 7. Backups and alerts

Two independent copies: an automated hourly off-host upload (7a) and a periodic
pull to a second machine (7b). Alerts (7c) make a failure actionable. Every
piece below is installed by `install.sh` but does nothing until the owner creates
the external resource and writes its URL to a root-owned file; this runbook never
creates buckets, PARs, policies, accounts or checks.

### 7a. Hourly upload to OCI Object Storage (owner-created bucket)

`trade-capture-backup.timer` (HH:25 UTC) runs `deploy/oci/capture_offsite.py backup`:
every COMPLETE, compacted partition of both roots not yet in its ledger
(`/var/lib/trade-capture-backup/uploaded-v1.ndjson`) is uploaded as one
deterministic tar object `r0/<partition path>.tar` or `u2/<partition path>.tar`.
OPEN, PARTIAL and not-yet-compacted partitions are never uploaded; a COMPLETE one
still uncompacted after 2 h makes the run fail (alert). So is a partition that
is not at the canonical path of its own contract, day and session, or whose
contract the label does not admit (`r0` = production contract, `u2` = universe
contracts; no other label exists). Each PUT carries `Content-MD5` (verified by
the service) and `If-None-Match: *` (an existing object is never overwritten).
The unit reads the archive read-only, runs at CPU weight 20 / nice 10 / idle I/O
beside the recorders' weight 1000, and holds no credential other than the PAR.

**What the run's output means.** Three ledger states, never merged:

| State | Set by | What it proves |
| :--- | :--- | :--- |
| `UPLOADED` | a PUT this client made that the service accepted | the bytes arrived (MD5-checked); not that they restore |
| `REMOTE_EXISTS_UNVERIFIED` | a PUT that met an existing object (409/412) | nothing: the write-only PAR cannot read it. Never re-sent, never counted as backed up, and `backup ATTENTION` (alert) every run while inside the budget window |
| `RESTORE_VERIFIED` | `accept-restore` with a matching drill receipt | the object was downloaded on another host, its SHA-256 equals the local partition's deterministic tar, and it replayed in full |

`backup OK` therefore means "accepted uploads, no anomaly"; only
`RESTORE_VERIFIED` means "recoverable off-host". A 409/412 normally means a PUT
whose response was lost or a ledger written before a crash; the next drill
resolves it (or proves the remote object is something else, and it stays
unverified).

**Storage budget.** Planned against **10 GB Standard** only, whatever else the
tenancy may include, with no listing from the VM:

| Item | Value |
| :--- | :--- |
| Growth (measured 2026-10-14) | ~0.56 GB/day U2 + ~0.22 GB/day R0 compacted = ~0.78 GB/day; volatile days higher |
| Lifecycle | delete after 5 days; a lifecycle run can be late, so up to ~7 days of objects may be present |
| Expected occupancy | ~0.78 x 5-7 days = 3.9-5.5 GB (39-55 % of 10 GB) |
| Guard (`--budget-window-days 7 --budget-bytes 8000000000`) | bytes the ledger shows sent in the last 7 days must stay <= 8.0 GB (80 %): it holds up to ~1.47x the measured rate sustained for a week; past that, uploads are withheld and the run alerts (no silent overage) |
| Horizon (`--horizon-days 4`) | a partition finalized > 4 days ago is never sent, so a lost ledger meets existing objects (409/412) rather than re-uploading history the lifecycle deleted |
| Requests | ~4 partitions/hour -> ~2,900 PUT/month, + one LIST page and one GET per object per drill (~500 every 3 days) |
| Cost | none expected inside a 10 GB Standard allowance; Billing > Cost Analysis is the check |

The guard is an estimate from the ledger: it cannot see objects older than the
window that a late lifecycle run has not yet deleted, so the bucket's size in
the Console is checked at every drill. Raise the budget or lengthen the
lifecycle only after confirming the tenancy's actual allowance; the three
numbers move together (horizon <= retention < lifecycle, window >= lifecycle + 2).

Owner steps (home region, same compartment as the VM):

1. Create bucket `trade-capture-backup` (Standard tier, private, no public
   access, no auto-tiering, object versioning off).
2. Retention rule: time-bound **4 days**, left **unlocked** during stage 1 (a
   leaked write PAR then still cannot overwrite a recent object; a locked rule
   can never be shortened or removed).
3. Lifecycle policy rule: delete objects after **5 days** (needs the IAM statement
   `Allow service objectstorage-<region> to manage object-family in compartment <c>`).
4. Pre-authenticated request on the bucket: **Permit object writes** only, no
   listing, expiry ~90 days (renew before it lapses: the run then fails and alerts).
5. On the VM, without echoing the URL into history or logs (activation, owner-run):
   ```bash
   sudo install -m 0640 -o root -g trade-capture /dev/null /etc/trade-capture/backup-par-url
   sudoedit /etc/trade-capture/backup-par-url      # paste https://.../p/<token>/n/<ns>/b/trade-capture-backup/o/
   sudo systemctl start trade-capture-backup.service && journalctl -u trade-capture-backup -n 30 --no-pager
   sudo systemctl enable --now trade-capture-backup.timer      # only after the first run reads backup OK
   ```

**Restore drill (every 3 days, with the 7b pull, on another machine).** A second
PAR on the bucket with **Permit object reads** and **Enable object listing**
(expiry of a day), its URL in a local file, and a copy of the VM's ledger:

```bash
ssh <vm> sudo cat /var/lib/trade-capture-backup/uploaded-v1.ndjson > ledger.ndjson
python deploy/oci/capture_offsite.py restore-verify --par-file <file> --destination <empty-dir> --ledger ledger.ndjson --receipt receipt.ndjson
scp receipt.ndjson <vm>:/tmp/receipt.ndjson
ssh <vm> sudo -u trade-capture /opt/trade-capture/python -I -B /opt/trade-capture/current/deploy/oci/capture_offsite.py accept-restore --state-dir /var/lib/trade-capture-backup --receipt /tmp/receipt.ndjson
```

Before fetching anything, each listed name must be an approved label plus the
canonical `v1/exchange=/instrument=/date=/session=` layout (traversal, other
labels and other shapes are refused unread). Each object must hash to the
ledger's SHA-256 for that name (an object the VM never recorded, or other
bytes under a recorded name, is refused), is extracted into the empty
directory under the path its name states (only the five files of a compacted
partition, as regular files, are accepted), fully replayed
(`verify_partition_v1`: hashes, contract, ordering, coverage, counts), and its
own contract, day and session must name exactly that path. Without `--ledger`
the drill still replays everything but cannot tell a substituted object whose
internal hashes were recomputed; its receipt says `ledger_match:false`.
`accept-restore` marks `RESTORE_VERIFIED` only where the receipt's SHA-256
equals the ledger's. The restored tree is an archive root:
`--root <empty-dir>/u2 universe-acceptance ...` works on it, and `backup` copies
it into the long-term Windows archive.

### 7b. Pull to a second machine

Pull finalized partitions to a second machine and re-verify them there:

```bash
rsync -a --ignore-existing <vm>:/srv/trade-capture/archive/ ~/oci-pull/     # needs rsync on both ends
python scripts/capture_bybit_public.py --root ~/oci-pull/capture backup <verified-destination>/capture
python scripts/capture_bybit_public.py --root ~/oci-pull/capture-universe-r1b backup <verified-destination>/capture-universe-r1b
```

Without rsync on the VM, `ssh <vm> tar -C /srv/trade-capture/archive -cf - . | tar -xf -`
into an empty directory works the same way. `backup` copies only COMPLETE
partitions and re-verifies every hash. This is the long-term copy: 7a keeps only
5 days, so pull at least every 3 days (well inside the 4-day retention), or a
VM loss can leave a hole that neither copy holds.

### 7c. Alerts (dead-man's switch)

A healthchecks.io-compatible check (`<url>` = success, `<url>/fail` = failure;
the free hosted tier or a self-hosted instance), owner-created, period 1 h,
grace 20 min. Its URL goes into `/etc/trade-capture/alert-ping-url` (same
`install`/`sudoedit` steps as 7a). Then:

* every health snapshot (HH:10) posts its full text -- `service` line and the
  `u2_hours` facts -- as a success ping, or as a `/fail` ping on `service ATTENTION`;
* `trade-capture-r0`, `-u2` and `-backup` entering the failed state (disk floor,
  held lock, start limit, failed upload) trigger `trade-capture-alert@<unit>`,
  one `/fail` ping naming the unit;
* silence alerts by itself: VM, timer or network down means no success ping.

Only status text leaves the VM (no market data, no credential). Coverage facts
ride along for visibility but never trigger an alert: COMPLETE hours stay
`universe-acceptance`'s question. No Telegram or other existing channel is used.

## 8. Recovery and rollback

| Symptom | Action |
| :--- | :--- |
| Unit failed, `last_exit=2` | Disk floor. Free space on the archive volume (offload verified partitions, or grow the volume), then `systemctl start`. Never lower `MIN_FREE_GB` to get going. |
| Unit failed, `last_exit=4` | Another recorder holds that root. Find it (`status`); never run two. |
| Unit failed, start limit hit | Ten crashes in an hour: `journalctl -u <unit>`, fix, `systemctl reset-failed <unit>`, start. |
| Units not started after boot | `/srv/trade-capture` not mounted (the assert held). Fix the attachment/fstab. |
| SELinux denial | `sudo ausearch --input-logs -m avc -ts recent`; re-run `install.sh` (restores labels). |
| Remove the deployment | `systemctl disable --now trade-capture-r0 trade-capture-u2 trade-capture-health.timer trade-capture-backup.timer`; the archive volume, its data and the bucket stay. |
| VM or volume lost | Rebuild (2-3); history comes back from the step 7 copies (`restore-verify`, then `backup`). Re-provisioning never bridges the hole. |
| Alert `/fail` from `trade-capture-alert@...` | `systemctl status <unit>`, `journalctl -u <unit> -n 50`; act on the row above for its exit code. |
| Backup run failed | `journalctl -u trade-capture-backup -n 30`: `STALE` = compaction failing (`compact` on that root); `put_failed:http_401/404` = PAR expired or revoked (owner renews, rewrite the file). |

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
