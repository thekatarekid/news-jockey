import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.portal_service import manage, service_config


class PortalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'Project with spaces'
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').touch()
        self.home = Path(self.temp.name) / 'home'
        self.agent = self.home / 'Library/LaunchAgents/com.newsjockey.portal.plist'

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def result(code=0, error=''):
        return subprocess.CompletedProcess([], code, '', error)

    def test_start_surfaces_registration_failure_without_claiming_running(self):
        with patch('tools.portal_service.sys.platform', 'darwin'), patch('tools.portal_service.healthy', return_value=False), patch('tools.portal_service.launchctl', side_effect=[self.result(113), self.result(), self.result(5, 'bootstrap error')]):
            with self.assertRaisesRegex(RuntimeError, 'Could not start the service: bootstrap error'):
                manage('start', self.root, self.home)

    def test_existing_foreground_server_is_not_replaced_by_duplicate(self):
        with patch('tools.portal_service.sys.platform', 'darwin'), patch('tools.portal_service.healthy', return_value=True), patch('tools.portal_service.launchctl', return_value=self.result(113)) as control:
            self.assertIn('outside the background service', manage('start', self.root, self.home))
            self.assertEqual(control.call_count, 1)
            self.assertFalse(self.agent.exists())

    def test_repeated_start_does_not_restart_healthy_service(self):
        self.agent.parent.mkdir(parents=True)
        self.agent.write_bytes(plistlib.dumps(service_config(self.root, self.home)))
        with patch('tools.portal_service.sys.platform', 'darwin'), patch('tools.portal_service.healthy', return_value=True), patch('tools.portal_service.launchctl', return_value=self.result()) as control:
            manage('start', self.root, self.home)
            self.assertEqual(control.call_count, 1)

    def test_failed_stop_keeps_registration_for_recovery(self):
        self.agent.parent.mkdir(parents=True)
        self.agent.write_bytes(plistlib.dumps(service_config(self.root, self.home)))
        with patch('tools.portal_service.sys.platform', 'darwin'), patch('tools.portal_service.launchctl', side_effect=[self.result(), self.result(1, 'stop error')]):
            with self.assertRaisesRegex(RuntimeError, 'Could not stop the service'):
                manage('stop', self.root, self.home)
        self.assertTrue(self.agent.exists())


if __name__ == '__main__':
    unittest.main()
