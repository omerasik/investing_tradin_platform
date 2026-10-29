"""Disposable synthetic recovery tests. No real Lite state, images or DB access."""

import copy
import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from scripts import terminal_lite_recovery as recovery


class PrivateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_preflight_reports_resources_and_missing_images_without_private_access(self):
        measured = {'available_physical_bytes': 2 * 1024**3, 'commit_headroom_bytes': 2 * 1024**3,
                    'disk_free_bytes': recovery.pilot.DISK_FLOOR - 1}
        with patch('scripts.research_loading_performance.host_memory', return_value=measured), \
                patch.object(recovery.subprocess, 'run', return_value=MagicMock(returncode=1)), \
                patch.object(recovery.pilot, 'settings') as settings:
            result = recovery.preflight()
        self.assertEqual('BLOCKED', result['status'])
        self.assertIn('HOST_RESOURCE_ADMISSION_REFUSED', result['blockers'])
        self.assertIn('EXACT_QUALIFIED_IMAGES_NOT_AVAILABLE_NO_REBUILD_OR_PULL', result['blockers'])
        self.assertFalse(result['private_operations_performed'])
        settings.assert_not_called()

    def test_deterministic_archive_roundtrip_and_source_preservation(self):
        source = self.root / 'source'
        (source / 'objects').mkdir(parents=True)
        (source / 'objects/fixture.bin').write_bytes(bytes(range(256)))
        (source / 'synthetic.json').write_text('{"synthetic": true}')
        original = recovery.file_inventory(source)
        first = recovery.pack_evidence(source, self.root / 'first.tar.gz')
        second = recovery.pack_evidence(source, self.root / 'second.tar.gz')
        self.assertEqual(first, second)
        self.assertEqual(original, recovery.file_inventory(source))
        self.assertEqual(original, recovery.unpack_evidence(self.root / 'first.tar.gz', self.root / 'clone', first))
        with self.assertRaisesRegex(ValueError, 'must_be_new'):
            recovery.unpack_evidence(self.root / 'first.tar.gz', self.root / 'clone', first)
        with self.assertRaises(FileExistsError):
            recovery.pack_evidence(source, self.root / 'first.tar.gz')
        with self.assertRaisesRegex(ValueError, 'outside_evidence'):
            recovery.pack_evidence(source, source / 'forbidden.tar.gz')
        self.assertFalse((source / 'forbidden.tar.gz').exists())

    def test_hash_tampering_and_unsafe_members_refuse_without_publishing(self):
        for name in ('../escape', '/absolute', 'C:/escape', 'a\\b', 'a//b'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unsafe'):
                recovery.safe_member(name)
        for kind in ('duplicate', 'link', 'missing'):
            archive = self.root / (kind + '.tgz')
            with tarfile.open(archive, 'w:gz') as output:
                entry = tarfile.TarInfo('safe')
                entry.size = 1
                if kind == 'link':
                    entry.type, entry.linkname = tarfile.SYMTYPE, '../escape'
                if kind != 'missing':
                    output.addfile(entry, io.BytesIO(b'x'))
                if kind == 'duplicate':
                    output.addfile(entry, io.BytesIO(b'x'))
            expected = {'sha256': recovery.digest_file(archive), 'files': {
                'safe': {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}}}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                recovery.unpack_evidence(archive, self.root / 'unpublished', expected)
            self.assertFalse((self.root / 'unpublished').exists())
        expected['sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'archive_hash_mismatch'):
            recovery.unpack_evidence(archive, self.root / 'unpublished', expected)

    def test_row_framing_binds_denominator_order_and_boundaries(self):
        self.assertNotEqual(recovery.row_digest([('ab',), ('c',)]), recovery.row_digest([('a',), ('bc',)]))
        self.assertNotEqual(recovery.row_digest([('a',), ('b',)]), recovery.row_digest([('b',), ('a',)]))
        self.assertEqual({'rows': 0, 'sha256': hashlib.sha256(b'').hexdigest()}, recovery.row_digest([]))

    def test_binary_transfer_preserves_null_and_non_utf8_bytes_and_exit_gate(self):
        measured = {'available_physical_bytes': 2 * 1024**3, 'commit_headroom_bytes': 2 * 1024**3,
                    'disk_free_bytes': recovery.pilot.DISK_FLOOR}
        path = self.root / 'synthetic.bin'
        with patch('scripts.research_loading_performance.host_memory', return_value=measured):
            with path.open('xb') as output:
                recovery.binary_transfer([sys.executable, '-c', 'import os; os.write(1, bytes(range(256)))'], destination=output)
            self.assertEqual(bytes(range(256)), path.read_bytes())
            with self.assertRaisesRegex(ValueError, 'transfer_failed'):
                recovery.binary_transfer([sys.executable, '-c', 'raise SystemExit(9)'])

    def test_target_identity_rejects_source_volume_labels_port_and_limits(self):
        token = 'abcdef123456'  # pragma: allowlist secret
        source = {'container_id': 'source-id', 'volume_sources': ['/source-volume']}
        target = {'Name': '/lite-recovery-' + token, 'Id': 'target-id',
                  'Config': {'Labels': {'lite.recovery.id': token}},
                  'HostConfig': {'PortBindings': {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5542'}]},
                                 'Memory': 256 * 1024**2, 'NanoCpus': 500000000},
                  'State': {'Running': True},
                  'Mounts': [{'Destination': '/var/lib/postgresql/data', 'Type': 'volume',
                              'Name': 'lite-recovery-' + token, 'Source': '/target-volume'}],
                  'NetworkSettings': {'Networks': {'new-network': {'IPAddress': '192.0.2.2'}}}}
        self.assertEqual('target-id', recovery.target_identity(target, token, source)['container_id'])
        mutations = [lambda t: t.update(Id='source-id'), lambda t: t['Mounts'][0].update(Source='/source-volume'),
                     lambda t: t['Config']['Labels'].clear(), lambda t: t['HostConfig'].update(Memory=0),
                     lambda t: t['HostConfig']['PortBindings']['5432/tcp'][0].update(HostPort='5441')]
        for mutate in mutations:
            changed = copy.deepcopy(target)
            mutate(changed)
            with self.assertRaisesRegex(ValueError, 'identity_forbidden'):
                recovery.target_identity(changed, token, source)

    def test_restore_refuses_original_cluster_before_binary_transfer(self):
        conn = MagicMock()
        conn.info.host, conn.info.port = '127.0.0.1', 5542
        conn.execute.return_value.fetchone.return_value = ('trade_terminal_lite', 'lite_owner', 160014, '192.0.2.2', 'source-cluster')
        with patch.object(recovery, 'require_private_admission'), \
                patch.object(recovery, 'private_artifact_path', return_value=Path('unused')), \
                patch.object(recovery, 'binary_transfer') as transfer:
            with self.assertRaisesRegex(ValueError, 'independent_recovery_cluster'):
                recovery.restore_verified_dump(conn, {'server_address': '192.0.2.2'}, 'source-cluster', 'unused', {})
            transfer.assert_not_called()

    def test_private_dump_paths_cannot_point_into_repo_or_source_evidence(self):
        with patch.object(recovery.pilot, 'STATE', self.root):
            self.assertEqual(self.root / 'transfer/run/snapshot.dump',
                             recovery.private_artifact_path(self.root / 'transfer/run/snapshot.dump'))
            for path in (self.root / 'evidence/snapshot.dump', recovery.ROOT / 'snapshot.dump', self.root / 'transfer'):
                with self.assertRaisesRegex(ValueError, 'private_transfer_artifact_path'):
                    recovery.private_artifact_path(path)


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('POSTGRES_TEST_DSN'),
                     'isolated synthetic GitHub PostgreSQL service required; never local Lite')
class RecoveryInventoryPostgresTests(unittest.TestCase):
    def test_consistent_table_inventory_and_non_mvcc_sequence_detection(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        dsn = os.environ['POSTGRES_TEST_DSN']
        fields = conninfo_to_dict(dsn)
        self.assertEqual(('127.0.0.1', '5432', 'trade_platform', 'postgres'),
                         tuple(fields.get(k) for k in ('host', 'port', 'dbname', 'user')))
        database_name = 'lite_recovery_fixture_' + uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as admin:
            self.assertTrue(160000 <= admin.info.server_version < 170000)
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
            try:
                isolated = make_conninfo(dsn, dbname=database_name)
                with psycopg.connect(isolated, autocommit=True) as writer:
                    writer.execute('CREATE TABLE synthetic(id serial PRIMARY KEY, label text)')
                    writer.execute("INSERT INTO synthetic(label) VALUES ('synthetic-only')")
                    with psycopg.connect(isolated) as reader:
                        reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                        before = recovery.sql_inventory(reader)
                        writer.execute("UPDATE synthetic SET label='changed-synthetic' WHERE id=1")
                        self.assertEqual(before, recovery.sql_inventory(reader))
                        writer.execute("SELECT nextval('synthetic_id_seq')")
                        after = recovery.sql_inventory(reader)
                        self.assertEqual(before['tables'], after['tables'])
                        self.assertNotEqual(before['sequences'], after['sequences'])
                    with psycopg.connect(isolated) as reader:
                        self.assertNotEqual(before['tables'], recovery.sql_inventory(reader)['tables'])
            finally:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database_name)))
