"""Opt-in, code-only GitHub-hosted qualification. Never run on a workstation.

Artifacts are explicitly constructed summaries, never dumps, fixtures, raw scan
reports, container inspect/env output, cookies or logs. No registry push.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
MIB = 1024**2
GIB = 1024**3
SCANNER = "aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c"  # pragma: allowlist secret
OUTPUT = ROOT / "lite-image-evidence"


def host():
    info = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        info[key] = int(value.strip().split()[0]) * 1024
    return {"available_bytes": info['MemAvailable'],
            "commit_headroom_bytes": info['MemAvailable'] + info.get('SwapFree', 0),
            "disk_free_bytes": shutil.disk_usage(ROOT).free, "cpus": os.cpu_count()}


def admitted(sample, initial):
    return (sample['available_bytes'] >= (5 * GIB if initial else 512 * MIB)
            and sample['commit_headroom_bytes'] >= (5 * GIB if initial else GIB)
            and sample['disk_free_bytes'] >= (12 * GIB if initial else 3 * GIB)
            and sample['cpus'] >= 2)


def admission():
    if (sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('RUNNER_OS') != 'Linux'):
        raise RuntimeError('requires_standard_clean_github_hosted_linux')
    sample = host()
    OUTPUT.mkdir(exist_ok=True)
    record = {"policy": "lite-code-only-ephemeral-linux-v1", "host": sample,
              "admitted": admitted(sample, True), "source_sha": os.environ['GITHUB_SHA']}
    (OUTPUT / 'admission.json').write_text(json.dumps(record, indent=2))
    if not record['admitted']:
        raise RuntimeError('runner_capacity_admission_refused')
    return sample


def scan_summary(report):
    counts = {}
    fixable = []
    secret_count = 0
    for result in report.get('Results') or []:
        secret_count += len(result.get('Secrets') or [])
        for item in result.get('Vulnerabilities') or []:
            severity = item['Severity']
            counts[severity] = counts.get(severity, 0) + 1
            if severity in ('HIGH', 'CRITICAL') and item.get('FixedVersion'):
                fixable.append({k: item.get(k) for k in
                                ('VulnerabilityID', 'PkgName', 'InstalledVersion', 'FixedVersion', 'Severity')})
    return {"severity_counts": counts, "fixable_high_critical": fixable,
            "secret_findings": secret_count,
            "eol": bool(report.get('Metadata', {}).get('OS', {}).get('EOSL'))}


def private_path(name):
    name = name.lstrip('./')
    return (name.startswith(('app/data/', 'app/evidence/', 'app/archive/', 'app/.trade_platform/', 'app/secrets/')) or name in ('app/.env', 'app/dashboard.config.json') or name.startswith('app/') and (name.endswith(('.parquet', '.dump', '.sqlite3', '.local.json')) or '/settings.local.' in name))


class Qualification:
    def __init__(self, temporary):
        self.temp = Path(temporary)
        self.evidence = {"source_sha": os.environ['GITHUB_SHA'], "fixture": "SYNTHETIC_ONLY",
                         "status": "BLOCKED", "steps": [], "images": {}, "resources": {},
                         "scanner": SCANNER, "host_minimum": admission()}
        self.secret_stage = False
        self.step = 'initialization'

    def run(self, label, args, *, timeout=120, env=None):
        self.step = label
        print(label, flush=True)
        started = time.monotonic()
        log = self.temp / 'command.log'
        with log.open('w+') as output:
            child = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
            try:
                while child.poll() is None:
                    sample = host()
                    for key in ('available_bytes', 'commit_headroom_bytes', 'disk_free_bytes'):
                        self.evidence['host_minimum'][key] = min(self.evidence['host_minimum'][key], sample[key])
                    if not admitted(sample, False) or time.monotonic() - started > timeout:
                        raise RuntimeError('runner_guard_or_step_deadline')
                    time.sleep(1)
                if child.returncode:
                    if not self.secret_stage:
                        output.seek(0)
                        print(output.read()[-6000:])
                    raise RuntimeError('qualification_command_failed')
                output.seek(0)
                return output.read()
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                self.evidence['steps'].append({"name": label, "seconds": round(time.monotonic() - started, 3),
                                               "exit_code": child.returncode})

    def measure(self, container):
        raw = self.run('measure_' + container, ['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.peak'])
        cpu = self.run('cpu_' + container, ['docker', 'exec', container, 'cat', '/sys/fs/cgroup/cpu.stat'])
        inspection = json.loads(self.run('state_' + container, ['docker', 'inspect', container]))[0]
        self.evidence['resources'][container] = {
            "image_id": inspection['Image'],
            "peak_cgroup_memory_bytes": int(raw.strip()), "oom_killed": inspection['State']['OOMKilled'],
            "memory_limit_bytes": inspection['HostConfig']['Memory'],
            "cpu_limit_nanocpus": inspection['HostConfig']['NanoCpus'],
            "cpu_quota": inspection['HostConfig']['CpuQuota'],
            "cpu_period": inspection['HostConfig']['CpuPeriod'],
            "cpu_stat": dict(line.split() for line in cpu.splitlines()),
            "restart_count": inspection['RestartCount']}
        assert not inspection['State']['OOMKilled'] and inspection['RestartCount'] == 0
        if container in ('lite-qual-api', 'lite-qual-dashboard'):
            name = container.removeprefix('lite-qual-')
            assert inspection['Image'] == self.evidence['images'][name]['image_id']

    def build_scan(self):
        self.run('create_bounded_builder', ['docker', 'buildx', 'create', '--name', 'lite-qual-builder',
                 '--driver', 'docker-container', '--driver-opt', 'memory=4g', '--driver-opt', 'memory-swap=4g',
                 '--driver-opt', 'cpu-period=100000', '--driver-opt', 'cpu-quota=200000', '--use'])
        for name, recipe, context in (('api', 'Dockerfile.lite', '.'), ('dashboard', 'web/Dockerfile', 'web')):
            tag = 'lite-qual-' + name + ':' + os.environ['GITHUB_SHA']
            self.run('build_' + name, ['docker', 'buildx', 'build', '--builder', 'lite-qual-builder',
                     '--load', '--provenance=false', '--tag', tag, '--file', recipe, context], timeout=900)
            self.measure('buildx_buildkit_lite-qual-builder0')
            self.evidence['resources']['build_' + name] = self.evidence['resources'].pop('buildx_buildkit_lite-qual-builder0')
            self.run('stop_builder_' + name, ['docker', 'buildx', 'stop', 'lite-qual-builder'])
            image = json.loads(self.run('inspect_image_' + name, ['docker', 'image', 'inspect', tag]))[0]
            assert image['Config']['User'] == '10001:10001'
            config = image['Config']
            assert config.get('Entrypoint') is None or config.get('Entrypoint') == ['docker-entrypoint.sh']
            assert config['Cmd'] == (['python', '-m', 'uvicorn', 'trade_platform.terminal_lite_v1:create_lite_app',
                    '--factory', '--host', '0.0.0.0', '--port', '8000', '--workers', '1',
                    '--limit-concurrency', '32', '--no-access-log'] if name == 'api' else
                    ['./node_modules/.bin/next', 'start', '-p', '3000'])
            assert not any(re.match(r'(LITE_READ_DSN|DATABASE_URL|POSTGRES_DSN|TRADE_PLATFORM_.*(?:TOKEN|SECRET))=', v)
                           for v in config.get('Env', []))
            if name == 'dashboard':
                assert 'NODE_ENV=production' in config['Env']
            archive = self.temp / 'image.tar'
            self.run('save_' + name, ['docker', 'save', '-o', str(archive), tag])
            layers = 0
            with tarfile.open(archive) as saved:
                for entry in saved:
                    if entry.name.endswith('/layer.tar') or entry.name.endswith('.tar'):
                        with tarfile.open(fileobj=saved.extractfile(entry)) as layer:
                            assert not any(private_path(item.name) for item in layer)
                        layers += 1
            # Docker's OCI archive may store layers as blobs; inspect those from manifest too.
            if not layers:
                with tarfile.open(archive) as saved:
                    manifest = json.load(saved.extractfile('manifest.json'))
                    for path in manifest[0]['Layers']:
                        with tarfile.open(fileobj=saved.extractfile(path)) as layer:
                            assert not any(private_path(item.name) for item in layer)
                        layers += 1
            assert layers > 0
            with archive.open('rb') as archive_file:
                archive_hash = hashlib.file_digest(archive_file, 'sha256').hexdigest()
            record = {"image_id": image['Id'], "layer_diff_ids": image['RootFS']['Layers'], "archive_sha256": archive_hash,
                      "size_bytes": image['Size'], "user": config['User'], "cmd": config['Cmd'],
                      "entrypoint": config.get('Entrypoint'), "layers_checked": layers}
            self.evidence['images'][name] = record
            for folder in ('cache', 'scan'):
                (self.temp / folder).mkdir(exist_ok=True)
            scanner = ['docker', 'run', '--rm', '--memory', '2g', '--memory-swap', '2g', '--cpus', '2',
                       '--user', f'{os.getuid()}:{os.getgid()}', '--read-only', '--cap-drop=ALL',
                       '--security-opt=no-new-privileges:true', '--tmpfs', '/tmp:rw,noexec,nosuid,size=1g',
                       '-v', f'{self.temp}:/input:ro', '-v', f'{self.temp / "cache"}:/cache:rw',
                       '-v', f'{self.temp / "scan"}:/output:rw', '-e', 'TRIVY_CACHE_DIR=/cache', SCANNER]
            self.run('scan_inventory_' + name, scanner + ['image', '--input', '/input/image.tar',
                     '--scanners', 'vuln,secret', '--exit-on-eol', '1', '--format', 'json',
                     '--output', '/output/report.json', '--no-progress'], timeout=600)
            record['scan'] = scan_summary(json.loads((self.temp / 'scan/report.json').read_text()))
            metadata = json.loads((self.temp / 'cache/db/metadata.json').read_text())
            record['scanner_database'] = {k: metadata.get(k) for k in ('Version', 'UpdatedAt', 'NextUpdate', 'DownloadedAt')}
            assert not record['scan']['secret_findings']
            self.run('scan_policy_' + name, scanner + ['image', '--input', '/input/image.tar',
                     '--scanners', 'vuln', '--ignore-unfixed', '--severity', 'HIGH,CRITICAL',
                     '--exit-code', '1', '--exit-on-eol', '1', '--no-progress'], timeout=300)
            archive.unlink()

    def request(self, url, *, token=None, data=None, method=None, opener=None, headers=None):
        request_headers = dict(headers or {})
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        if data is not None:
            data = json.dumps(data).encode()
            request_headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            response = (opener or urllib.request.build_opener()).open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    def smoke(self):
        from datetime import date

        import psycopg

        from scripts.terminal_lite import initialize_database, prepare_subset
        from tests.lite_fixtures import seed_lite_days

        self.secret_stage = True
        self.step = 'generate_synthetic_fixture'
        values = {key: secrets.token_hex(32) for key in ('owner', 'reader', 'operator', 'view', 'session')}
        for value in values.values():
            print('::add-mask::' + value, flush=True)
        for name in ('api', 'dashboard'):
            tag = 'lite-qual-' + name + ':' + os.environ['GITHUB_SHA']
            configuration = self.run('credential_absence_' + name, ['docker', 'image', 'inspect', tag])
            history = self.run('history_' + name, ['docker', 'history', '--no-trunc', tag])
            assert all(value not in configuration + history for value in values.values())
        fixture = self.temp / 'synthetic'
        fixture.mkdir()
        seed_lite_days(fixture, date(2026, 7, 20), 1)
        prepare_subset(fixture / 'archive', fixture / 'source', fixture / 'evidence', date(2026, 7, 20))
        self.run('network', ['docker', 'network', 'create', 'lite-qual-net'])
        pg_env = self.temp / 'postgres.env'
        pg_env.write_text('POSTGRES_DB=trade_terminal_lite\nPOSTGRES_USER=lite_owner\nPOSTGRES_PASSWORD=' + values['owner'] + '\n')
        self.run('start_pg16', ['docker', 'run', '-d', '--name', 'lite-qual-db', '--network', 'lite-qual-net',
                 '--memory', '256m', '--memory-swap', '256m', '--cpus', '0.5', '--pids-limit', '128',
                 '--env-file', str(pg_env), '-p', '127.0.0.1:5541:5432', 'postgres:16-alpine',
                 '-c', 'shared_buffers=32MB', '-c', 'work_mem=2MB', '-c', 'max_connections=12',
                 '-c', 'max_parallel_workers=0'])
        owner_dsn = f"postgresql://lite_owner:{values['owner']}@127.0.0.1:5541/trade_terminal_lite"
        for _ in range(30):
            try:
                with psycopg.connect(owner_dsn, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                time.sleep(1)
        initialize_database(owner_dsn, values['reader'])
        with psycopg.connect(f"postgresql://lite_reader:{values['reader']}@127.0.0.1:5541/trade_terminal_lite") as conn:
            for statement in ('CREATE TABLE forbidden(x int)', 'DELETE FROM strategy_lab_studies'):
                try:
                    conn.execute(statement)
                except (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege):
                    conn.rollback()
                else:
                    raise AssertionError('reader_mutation_not_refused')
        api_env = self.temp / 'api.env'
        api_env.write_text(f"LITE_READ_DSN=postgresql://lite_reader:{values['reader']}@lite-qual-db:5432/trade_terminal_lite\n"
                           f"LITE_RESEARCH_ROOT=/evidence\nTRADE_PLATFORM_OPERATOR_TOKEN={values['operator']}\n")
        tag = ':' + os.environ['GITHUB_SHA']
        self.run('start_api', ['docker', 'run', '-d', '--name', 'lite-qual-api', '--network', 'lite-qual-net',
                 '--memory', '512m', '--memory-swap', '512m', '--cpus', '1', '--pids-limit', '128',
                 '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                 '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', '--env-file', str(api_env),
                 '-v', f'{fixture / "evidence"}:/evidence:ro', '-p', '127.0.0.1:8767:8000',
                 '-p', '127.0.0.1:8768:3000', 'lite-qual-api' + tag])
        self.wait('http://127.0.0.1:8767/health/lite-ready')
        status, _, body = self.request('http://127.0.0.1:8767/health/lite-ready')
        ready = json.loads(body)
        assert status == 200 and ready['environment'] == 'lite'
        assert all(ready[key] is False for key in ('paper_execution_enabled', 'live_trading_enabled', 'research_commands_enabled'))
        path = 'http://127.0.0.1:8767/operator-dashboard/strategy-lab/studies'
        assert self.request(path)[0] == 401
        assert self.request(path, token='invalid-generated-test-token')[0] == 401
        assert self.request(path, token=values['operator'])[0] == 200
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            response = self.request(path, token=values['operator'], data={}, method=method)
            assert response[0] == 403 and 'LITE_READ_ONLY' in response[2]
        catalog = json.loads(self.request('http://127.0.0.1:8767/operator-dashboard/chart-series', token=values['operator'])[2])
        assert len(catalog['items']) == 3
        for item in catalog['items']:
            chart = self.request('http://127.0.0.1:8767/operator-dashboard/chart-series/' + item['manifest_hash'] + '/candles?timeframe=1h', token=values['operator'])
            assert chart[0] == 200 and len(json.loads(chart[2])['candles']) == 24
        web_env = self.temp / 'web.env'
        web_env.write_text(f"TRADE_PLATFORM_LITE=1\nTRADE_PLATFORM_API_BASE_URL=http://127.0.0.1:8000\n"
                           f"TRADE_PLATFORM_DASHBOARD_ORIGIN=http://127.0.0.1:8768\n"
                           f"TRADE_PLATFORM_OPERATOR_TOKEN={values['operator']}\n"
                           f"TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN={values['view']}\n"
                           f"TRADE_PLATFORM_SESSION_SECRET={values['session']}\n"
                           "NODE_OPTIONS=--max-old-space-size=512\nNEXT_TELEMETRY_DISABLED=1\n")  # pragma: allowlist secret
        # Shared container network namespace makes loopback API URLs valid without weakening HTTPS policy.
        self.run('start_dashboard', ['docker', 'run', '-d', '--name', 'lite-qual-dashboard', '--network', 'container:lite-qual-api',
                 '--memory', '2g', '--memory-swap', '2g', '--cpus', '1', '--pids-limit', '128',
                 '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--env-file', str(web_env),
                 'lite-qual-dashboard' + tag])
        # The image's unchanged default command listens on 3000.
        base = 'http://127.0.0.1:8768'
        self.wait(base + '/login')
        assert self.request(base + '/api/authorities?target=/operator-dashboard/strategy-lab/studies')[0] == 401
        assert self.request(base + '/api/auth/login', data={'credential': 'incorrect'})[0] == 401
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        assert self.request(base + '/api/auth/login', data={'credential': values['view']}, opener=opener)[0] == 200
        cookie = next(iter(jar))
        assert cookie.name == 'dashboard_session' and cookie.has_nonstandard_attr('HttpOnly')
        for page in ('/terminal', '/strategy-lab', '/evidence'):
            status, headers, body = self.request(base + page, opener=opener)
            assert status == 200 and headers.get('Content-Security-Policy') and headers.get('X-Frame-Options') == 'DENY'
            assert all(value not in body for value in values.values())
        status, _, payload = self.request(base + '/api/authorities?target=/operator-dashboard/strategy-lab/studies', opener=opener)
        assert status == 200 and json.loads(payload)['total'] == 0
        assert self.request(base + '/api/terminal/commands', data={}, opener=opener)[0] == 403
        assert self.request(base + '/api/auth/logout', data={}, opener=opener)[0] in (200, 303)
        assert not list(jar)
        # Verify Secure cookie generation under the trusted HTTPS-forwarding contract without public ingress.
        status, headers, _ = self.request(base + '/api/auth/login', data={'credential': values['view']},
                                         headers={'x-forwarded-proto': 'https'})
        assert status == 200 and 'Secure' in headers['Set-Cookie'] and 'SameSite=lax' in headers['Set-Cookie']
        for container in ('lite-qual-db', 'lite-qual-api', 'lite-qual-dashboard'):
            assert self.run('uid_' + container, ['docker', 'exec', container, 'id', '-u']).strip() == ('0' if container == 'lite-qual-db' else '10001')
            self.measure(container)
        self.evidence['smoke'] = {"status": "PASS", "synthetic_markets": 3, "studies": 0,
                                  "api_auth": "PASS", "reader_grants": "SELECT_ONLY", "mutations": "REFUSED",
                                  "production_dashboard": "PASS", "login_logout_cookie_https_contract": "PASS",
                                  "execution": "DISABLED", "public_ingress": "NONE"}

    def wait(self, url):
        self.step = 'health_api' if ':8767/' in url else 'health_dashboard'
        started = time.monotonic()
        while time.monotonic() - started < 60:
            if not admitted(host(), False):
                raise RuntimeError('runtime_host_admission_refused')
            try:
                if self.request(url)[0] == 200:
                    self.evidence['steps'].append({"name": self.step, "seconds": round(time.monotonic() - started, 3),
                                                   "status": "PASS"})
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise RuntimeError('production_health_deadline')

    def cleanup(self):
        for name in ('lite-qual-dashboard', 'lite-qual-api', 'lite-qual-db'):
            subprocess.run(['docker', 'rm', '-f', '-v', name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        subprocess.run(['docker', 'network', 'rm', 'lite-qual-net'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        subprocess.run(['docker', 'buildx', 'rm', 'lite-qual-builder'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def main():
    if sys.argv[1:] == ['admission']:
        admission()
        return
    if sys.argv[1:] != ['qualify']:
        raise ValueError('expected_admission_or_qualify')
    admission()
    with tempfile.TemporaryDirectory(prefix='lite-qualification-', dir=os.environ['RUNNER_TEMP']) as temporary:
        qualification = Qualification(temporary)
        try:
            qualification.build_scan()
            qualification.smoke()
            qualification.evidence['status'] = 'IMAGE_QUALIFICATION_PASS'
        except Exception as error:
            qualification.evidence['failure'] = {"step": qualification.step, "type": type(error).__name__}
            raise
        finally:
            try:
                qualification.cleanup()
            finally:
                (OUTPUT / 'qualification.json').write_text(json.dumps(qualification.evidence, indent=2))


if __name__ == '__main__':
    main()
