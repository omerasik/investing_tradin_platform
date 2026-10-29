"""Private recovery primitives; no builds, pulls, research, uploads or source writes.

The CLI is admission-only. These helpers do not claim end-to-end recovery:
the runbook's bounded canonical audits, independent target and production smoke
must also pass before PRIVATE_RECOVERY_PASS may be issued.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from scripts import terminal_lite_research as pilot

API_IMAGE = 'sha256:5751700f3b7b139ccae3e3daafc7bac32da7603cd589424ee1e1a7a713cbd941'  # pragma: allowlist secret
DASHBOARD_IMAGE = 'sha256:b1ac5cbc604a81d0d3613b9b4c276680919560314a6a043931dc09353d4046be'  # pragma: allowlist secret
MAX_EVIDENCE = 16 * 1024**2


def preflight(operation='export', *, image_ids=None):
    """Capacity precheck only; identity/integrity and runtime acceptance are separate.

    Historical CI config IDs are records, not retrievable image archives. Only
    smoke requires locally available images from a separately qualified build.
    """
    from scripts.research_loading_performance import host_memory

    if operation not in ('export', 'restore', 'smoke'):
        raise ValueError('unknown_recovery_operation')
    if image_ids is not None and operation != 'smoke':
        raise ValueError('production_images_apply_only_to_smoke')
    if image_ids is not None and (set(image_ids) != {'api', 'dashboard'} or any(
            not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value)
            for value in image_ids.values())):
        raise ValueError('explicit_production_image_ids_required')
    measured = host_memory()
    images = {}
    blockers = []
    if os.name != 'nt':
        blockers.append('PRIVATE_ACCEPTANCE_REQUIRES_ADMITTED_WINDOWS_HOST')
    if not pilot.admitted(measured, initial=True):
        blockers.append('HOST_RESOURCE_ADMISSION_REFUSED')
    if operation == 'smoke':
        if image_ids is None:
            blockers.append('SEPARATELY_QUALIFIED_LOCAL_PRODUCTION_IMAGES_REQUIRED')
        else:
            for key, digest in image_ids.items():
                try:
                    check = subprocess.run(
                        ['docker', 'image', 'inspect', digest, '--format', '{{.Id}}'],
                        capture_output=True, text=True, check=False, timeout=10)
                    available = check.returncode == 0 and check.stdout.strip() == digest
                except (OSError, subprocess.TimeoutExpired):
                    available = False
                images[key] = {'expected_id': digest, 'available': available}
            if not all(item['available'] for item in images.values()):
                blockers.append('PRODUCTION_IMAGES_NOT_AVAILABLE_NO_REBUILD_OR_PULL')
    prerequisites = {
        'export': ['SOURCE_CLUSTER_PIN_AND_CANONICAL_AUDIT', 'READ_ONLY_CONSISTENT_SNAPSHOT',
                   'PRIVATE_DESTINATION_ACL_AND_INTEGRITY'],
        'restore': ['VERIFIED_EXPORT_AND_EVIDENCE', 'INDEPENDENT_EMPTY_PG16_TARGET',
                    'FULL_LOGICAL_AND_CANONICAL_RECONCILIATION'],
        'smoke': ['SEPARATE_IMAGE_QUALIFICATION_RECEIPT', 'RECONCILED_PRIVATE_RESTORE',
                  'INDEPENDENT_SELECT_ONLY_READER', 'AUTHENTICATED_RUNTIME_AND_MUTATION_REFUSAL'],
    }
    return {'status': 'BLOCKED' if blockers else 'PREFLIGHT_PASS_ACCEPTANCE_NOT_RUN',
            'operation': operation, 'host': measured, 'images': images, 'blockers': blockers,
            'historical_qualification_records': {'run_id': '40880602729',
                                                'api': API_IMAGE, 'dashboard': DASHBOARD_IMAGE},
            'acceptance_prerequisites_not_verified': prerequisites[operation],
            'limits': {'initial_ram_bytes': 1024**3, 'runtime_ram_bytes': pilot.MEMORY_BYTES,
                       'commit_headroom_bytes': 1024**3, 'disk_floor_bytes': pilot.DISK_FLOOR},
            'private_operations_performed': False}


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def is_link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def require_private_admission(operation):
    result = preflight(operation)
    if result['status'] != 'PREFLIGHT_PASS_ACCEPTANCE_NOT_RUN':
        raise ValueError('private_recovery_preconditions_refused')


def private_artifact_path(path):
    path = Path(path)
    private_root = (pilot.STATE / 'transfer').resolve()
    if (is_link(path) or any(is_link(parent) for parent in path.parents)
            or private_root not in path.resolve().parents):
        raise ValueError('private_transfer_artifact_path_required')
    return path


def binary_transfer(args, *, source=None, destination=None):
    """No text conversion or diagnostic/credential output; enforce host floors."""
    from scripts.research_loading_performance import host_memory

    started = time.monotonic()
    child = subprocess.Popen(args, stdin=source, stdout=destination or subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        while child.poll() is None:
            if not pilot.admitted(host_memory(), initial=False) or time.monotonic() - started > 120:
                raise ValueError('private_transfer_host_guard_or_deadline')
            time.sleep(0.25)
        if child.returncode:
            raise ValueError('private_transfer_failed')
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def file_inventory(root):
    root = Path(root)
    if is_link(root) or not root.is_dir():
        raise ValueError('regular_evidence_directory_required')
    result = {}
    total = 0
    for path in sorted(root.rglob('*')):
        if is_link(path):
            raise ValueError('evidence_links_forbidden')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('evidence_special_file_forbidden')
        total += path.stat().st_size
        if total > MAX_EVIDENCE:
            raise ValueError('evidence_size_ceiling')
        result[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size,
                                                   'sha256': digest_file(path)}
    return result


def pack_evidence(root, destination):
    """Deterministic bytes, exclusive destination, source remains untouched."""
    if Path(root).resolve() in Path(destination).resolve().parents:
        raise ValueError('archive_must_be_outside_evidence')
    before = file_inventory(root)
    with (Path(destination).open('xb') as output,
          gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed,
          tarfile.open(fileobj=compressed, mode='w') as archive):
        for name, record in before.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = record['bytes'], 0o444, 0
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ''
            with (Path(root) / name).open('rb') as stream:
                archive.addfile(entry, stream)
    if file_inventory(root) != before:
        raise ValueError('source_evidence_changed_during_pack')
    return {'sha256': digest_file(destination), 'files': before}


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute()
            or '..' in path.parts or path.as_posix() != name or name in ('.', '..')):
        raise ValueError('unsafe_evidence_member')
    return path


def unpack_evidence(archive_path, destination, expected):
    destination = Path(destination)
    if destination.exists() or is_link(destination) or any(is_link(p) for p in destination.parents):
        raise ValueError('restore_directory_must_be_new')
    if digest_file(archive_path) != expected['sha256']:
        raise ValueError('evidence_archive_hash_mismatch')
    with tempfile.TemporaryDirectory(prefix='.recovery-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'evidence'
        staging.mkdir()
        seen, total = set(), 0
        with tarfile.open(archive_path, 'r:gz') as archive:
            for member in archive:
                safe_member(member.name)
                if not member.isfile() or member.name in seen or member.name not in expected['files']:
                    raise ValueError('unexpected_or_duplicate_evidence_member')
                seen.add(member.name)
                total += member.size
                if total > MAX_EVIDENCE or member.size != expected['files'][member.name]['bytes']:
                    raise ValueError('evidence_size_mismatch')
                path = staging / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('xb') as output:
                    shutil.copyfileobj(source, output, length=65536)
        if file_inventory(staging) != expected['files']:
            raise ValueError('restored_evidence_inventory_mismatch')
        staging.rename(destination)
    return file_inventory(destination)


def row_digest(rows):
    digest = hashlib.sha256()
    count = 0
    for (value,) in rows:
        encoded = value.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        count += 1
    return {'rows': count, 'sha256': digest.hexdigest()}


def sql_inventory(connection):
    """Caller retains a repeatable-read/read-only transaction; no DML/DDL."""
    from psycopg import sql

    for setting in ("SET LOCAL TIME ZONE 'UTC'", "SET LOCAL DateStyle='ISO'",
                    "SET LOCAL IntervalStyle='postgres'", "SET LOCAL extra_float_digits=3"):
        connection.execute(setting)
    tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename COLLATE \"C\"").fetchall()
    result = {'tables': {}, 'sequences': {}}
    for (table,) in tables:
        # Server-side cursor bounds client memory even when one table grows.
        with connection.cursor(name='recovery_inventory') as cursor:
            cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"')
                           .format(sql.Identifier('public', table)))
            result['tables'][table] = row_digest(cursor)
    sequences = connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename COLLATE \"C\"").fetchall()
    for (sequence,) in sequences:
        value = connection.execute(sql.SQL('SELECT last_value,is_called FROM {}')
                                   .format(sql.Identifier('public', sequence))).fetchone()
        result['sequences'][sequence] = list(value)
    return result


def target_identity(container, recovery_id, source):
    """Refuse arbitrary/existing/source containers before any target mutation."""
    if not re.fullmatch(r'[a-f0-9]{12}', recovery_id):
        raise ValueError('recovery_id_invalid')
    name = 'lite-recovery-' + recovery_id
    config = container['HostConfig']
    volumes = [v for v in container['Mounts'] if v['Destination'] == '/var/lib/postgresql/data']
    networks = list(container['NetworkSettings']['Networks'].values())
    if (container['Name'] != '/' + name or container['Id'] == source['container_id']
            or container['Config']['Labels'].get('lite.recovery.id') != recovery_id
            or config['PortBindings'] != {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5542'}]}
            or config['Memory'] != 256 * 1024**2 or config['NanoCpus'] != 500000000
            or not container['State']['Running'] or len(networks) != 1 or not networks[0]['IPAddress']
            or len(volumes) != 1 or volumes[0].get('Type') != 'volume'
            or volumes[0].get('Name') != name or volumes[0]['Source'] in source['volume_sources']):
        raise ValueError('recovery_target_identity_forbidden')
    return {'container_id': container['Id'], 'name': name, 'volume': name,
            'server_address': networks[0]['IPAddress']}


def verify_target_database(connection, target, source_system_identifier, *, empty):
    if (connection.info.host, connection.info.port) != ('127.0.0.1', 5542):
        raise ValueError('recovery_target_loopback_port_required')
    row = connection.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int,"
                             "host(inet_server_addr()),system_identifier::text FROM pg_control_system()").fetchone()
    if (row[:2] != ('trade_terminal_lite', 'lite_owner') or not 160000 <= row[2] < 170000
            or row[3] != target['server_address'] or row[4] == source_system_identifier):
        raise ValueError('independent_recovery_cluster_required')
    if empty and connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone() != (0,):
        raise ValueError('recovery_target_must_be_empty')
    return row[4]


def export_snapshot(connection, destination):
    """Binary-safe source-only dump and inventory within one retained snapshot.

    Call only after host admission and bounded canonical source audit. Caller
    owns the REPEATABLE READ/READ ONLY transaction and exclusive private folder.
    """
    require_private_admission('export')
    destination = private_artifact_path(destination)
    if (connection.info.host, connection.info.port) != ('127.0.0.1', 5441):
        raise ValueError('source_loopback_port_required')
    expected = pilot.container_identity()
    row = connection.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int,"
                             "host(inet_server_addr()),system_identifier::text FROM pg_control_system()").fetchone()
    pin = json.loads((pilot.STATE / 'research-cluster.local.json').read_text())
    if (row[:2] != ('trade_terminal_lite', 'lite_owner') or not 160000 <= row[2] < 170000
            or row[3] != expected['server_address'] or pin != {
                'database': row[0], 'writer': row[1], 'system_identifier': row[4]}):
        raise ValueError('source_cluster_pin_mismatch')
    identity = connection.execute('SELECT current_database(),current_user').fetchone()
    if identity != ('trade_terminal_lite', 'lite_owner'):
        raise ValueError('source_identity_forbidden')
    if connection.execute('SHOW transaction_read_only').fetchone() != ('on',):
        raise ValueError('source_transaction_must_be_read_only')
    if connection.execute('SHOW transaction_isolation').fetchone() != ('repeatable read',):
        raise ValueError('source_transaction_must_be_repeatable_read')
    if connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                          "AND usename='lite_owner' AND pid<>pg_backend_pid()").fetchone() != (0,):
        raise ValueError('source_writer_connections_must_be_quiescent')
    snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
    inventory = sql_inventory(connection)
    with Path(destination).open('xb') as output:
        binary_transfer(['docker', 'exec', 'trade-terminal-lite-postgres-1', 'pg_dump', '-U', 'lite_owner',
                        '-d', 'trade_terminal_lite', '--format=custom', '--no-owner', '--no-acl',
                        '--snapshot=' + snapshot], destination=output)
    if sql_inventory(connection) != inventory:
        raise ValueError('source_sequence_or_inventory_changed')
    if pilot.container_identity() != expected:
        raise ValueError('source_container_changed')
    return {'sha256': digest_file(destination), 'bytes': Path(destination).stat().st_size, 'inventory': inventory}


def restore_verified_dump(connection, target, source_system_identifier, dump_path, expected):
    """Target identity proof must be obtained by target_identity immediately before this call."""
    require_private_admission('restore')
    dump_path = private_artifact_path(dump_path)
    verify_target_database(connection, target, source_system_identifier, empty=True)
    source_container = json.loads(subprocess.check_output(['docker', 'inspect', 'trade-terminal-lite-postgres-1'], timeout=10))[0]
    actual = json.loads(subprocess.check_output(['docker', 'inspect', target['container_id']], timeout=10))[0]
    recovery_id = target['name'].removeprefix('lite-recovery-')
    verified = target_identity(actual, recovery_id, {
        'container_id': source_container['Id'], 'volume_sources': [v['Source'] for v in source_container['Mounts']]})
    if verified != target:
        raise ValueError('recovery_target_changed_before_restore')
    if digest_file(dump_path) != expected['sha256']:
        raise ValueError('dump_hash_mismatch')
    with Path(dump_path).open('rb') as source:
        binary_transfer(['docker', 'exec', '-i', target['name'], 'pg_restore', '-U', 'lite_owner',
                        '-d', 'trade_terminal_lite', '--no-owner', '--no-acl', '--exit-on-error',
                        '--single-transaction'], source=source)
    if sql_inventory(connection) != expected['inventory']:
        raise ValueError('restored_sql_inventory_mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight'])
    parser.add_argument('--operation', choices=['export', 'restore', 'smoke'], default='export')
    parser.add_argument('--api-image-id')
    parser.add_argument('--dashboard-image-id')
    args = parser.parse_args()
    image_ids = None
    if args.api_image_id is not None or args.dashboard_image_id is not None:
        image_ids = {'api': args.api_image_id, 'dashboard': args.dashboard_image_id}
    result = preflight(args.operation, image_ids=image_ids)
    print(json.dumps(result))
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
