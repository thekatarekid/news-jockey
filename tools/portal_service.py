"""Start and stop this local portal through the current user's macOS launchd."""
import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.newsjockey.portal'
URL = 'http://127.0.0.1:8765'


def launchctl(*args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=10)


def healthy():
    try:
        with urlopen(URL + '/api/processing', timeout=5) as response:
            data = json.load(response)
        return isinstance(data, dict) and isinstance(data.get('extraction'), dict) and isinstance(data.get('versions'), int)
    except (URLError, OSError, ValueError):
        return False


def service_config(root, home=None):
    log = (home or Path.home()) / 'Library/Logs/NewsJockey/service.log'
    return {
        'Label': LABEL,
        'ProgramArguments': [str(root / '.venv/bin/python'), str(root / 'app.py')],
        'WorkingDirectory': str(root),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        # An interactive local portal needs normal disk scheduling; Background
        # applies macOS I/O throttling to its database and HTTP request threads.
        'ProcessType': 'Standard',
        'StandardOutPath': str(log),
        'StandardErrorPath': str(log),
    }


def manage(action, root=ROOT, home=None):
    if sys.platform != 'darwin':
        raise RuntimeError('This service launcher requires macOS. Run python app.py directly elsewhere.')
    domain = f'gui/{os.getuid()}'
    target = domain + '/' + LABEL
    agent = (home or Path.home()) / 'Library/LaunchAgents' / (LABEL + '.plist')
    loaded = launchctl('print', target).returncode == 0

    if action == 'status':
        return f"Portal {'responding' if healthy() else 'not responding'} at {URL}; background service {'loaded' if loaded else 'stopped'}."
    if action == 'stop':
        if loaded:
            result = launchctl('bootout', target)
            if result.returncode:
                raise RuntimeError('Could not stop the service: ' + result.stderr.strip())
        # Removing our registration also prevents another start at login.
        agent.unlink(missing_ok=True)
        return 'News Jockey background service stopped. Run start.command to start it again.'

    if not loaded and healthy():
        return 'News Jockey is already running outside the background service. Stop that server before starting the service.'
    config = service_config(root, home)
    if not (root / '.venv/bin/python').exists():
        raise RuntimeError('Python environment missing. Run start.command first.')
    (root / 'data').mkdir(exist_ok=True)
    Path(config['StandardOutPath']).parent.mkdir(parents=True, exist_ok=True)
    agent.parent.mkdir(parents=True, exist_ok=True)
    old = plistlib.loads(agent.read_bytes()) if agent.exists() else None
    if loaded and old != config:
        result = launchctl('bootout', target)
        if result.returncode:
            raise RuntimeError('Could not reload the service: ' + result.stderr.strip())
        loaded = False
    agent.write_bytes(plistlib.dumps(config))
    agent.chmod(0o600)
    if not loaded:
        result = launchctl('enable', target)
        if result.returncode:
            raise RuntimeError('Could not enable the service: ' + result.stderr.strip())
        result = launchctl('bootstrap', domain, str(agent))
        if result.returncode:
            raise RuntimeError('Could not start the service: ' + result.stderr.strip())
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if healthy():
            return f'News Jockey is running at {URL} as a Mac background service. It also starts at login.'
        time.sleep(0.5)
    raise RuntimeError(f"The service was registered but the portal did not respond. Check {config['StandardErrorPath']}.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'], nargs='?', default='start')
    args = parser.parse_args()
    try:
        print(manage(args.action))
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
