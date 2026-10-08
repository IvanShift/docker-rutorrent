"""Host checks of the shared smoke SCGI client and torrent fixture generator.

A local Unix-socket peer stands in for the daemon, so whole-reply reading,
truncation, silence and faults are exercised without a container.
"""

import hashlib
import re
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "tests/smoke/service-rpc.php"
FIXTURE = ROOT / "tests/smoke/torrent-fixture.php"


def scgi_request(connection):
    """Read one complete SCGI netstring request and return (headers, body)."""
    data = b""
    while b":" not in data:
        data += connection.recv(4096)
    length, rest = data.split(b":", 1)
    while len(rest) < int(length) + 1:
        rest += connection.recv(4096)
    headers = rest[: int(length)].split(b"\0")[:-1]
    headers = dict(zip(headers[::2], headers[1::2]))
    assert rest[int(length) : int(length) + 1] == b",", "netstring terminator"
    body = rest[int(length) + 1 :]
    while len(body) < int(headers[b"CONTENT_LENGTH"]):
        body += connection.recv(4096)
    return headers, body


def xml_reply(value):
    body = ('<?xml version="1.0"?><methodResponse><params><param><value><string>'
            f"{value}</string></value></param></params></methodResponse>").encode()
    return b"Status: 200 OK\r\nContent-Type: text/xml\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)


@requires_host_php("the smoke SCGI client", extensions=("SimpleXML",))
class ServiceRpcClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smoke-rpc-")
        self.addCleanup(self.temp.cleanup)
        self.socket_path = Path(self.temp.name) / "rtorrent.sock"
        self.requests = []

    def serve(self, respond):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        server.listen(1)
        self.addCleanup(server.close)

        def run():
            connection, _ = server.accept()
            with connection:
                self.requests.append(scgi_request(connection))
                respond(connection)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)

    def call(self, *args):
        wrapper = Path(self.temp.name) / "client.php"
        wrapper.write_text(f"<?php\n$serviceRpcSocket = {str(self.socket_path)!r};\n"
                           f"$serviceRpcTimeout = 1;\nrequire {str(CLIENT)!r};\n")
        return subprocess.run(["php", str(wrapper), *args], capture_output=True, text=True, timeout=20)

    def test_whole_reply_beyond_64_kib_is_read_and_request_is_framed(self):
        value = "x" * (200 * 1024)
        self.serve(lambda connection: connection.sendall(xml_reply(value)))
        result = self.call("d.name", "ABC&<")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, value + "\n", ""))
        headers, body = self.requests[0]
        self.assertEqual(headers[b"SCGI"], b"1")
        self.assertEqual(int(headers[b"CONTENT_LENGTH"]), len(body))
        self.assertIn(b"<methodName>d.name</methodName>", body)
        self.assertIn(b"<string>ABC&amp;&lt;</string>", body)

    def test_truncated_reply_is_reported(self):
        def truncated(connection):
            connection.sendall(xml_reply("complete")[:-10])

        self.serve(truncated)
        result = self.call("system.pid")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"service-rpc: truncated reply to system\.pid: Content-Length \d+, received \d+ bytes")

    def test_silent_daemon_is_reported_after_the_deadline(self):
        released = threading.Event()
        self.addCleanup(released.set)
        self.serve(lambda connection: released.wait(5))
        result = self.call("system.pid")
        released.set()
        self.assertEqual(result.returncode, 1)
        self.assertIn("service-rpc: no complete reply to system.pid within 1s (0 bytes received)", result.stderr)

    def test_fault_and_missing_socket_are_visible(self):
        fault = (b'<?xml version="1.0"?><methodResponse><fault><value><struct><member><name>faultString</name>'
                 b"<value><string>Unsupported target type found.</string></value></member></struct></value>"
                 b"</fault></methodResponse>")
        self.serve(lambda connection: connection.sendall(
            b"Status: 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(fault), fault)))
        result = self.call("d.name", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported target type found.", result.stderr)
        self.socket_path.unlink()
        result = self.call("system.pid")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"service-rpc: cannot connect to {self.socket_path} for system.pid", result.stderr)


@requires_host_php("the smoke torrent fixture")
class TorrentFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smoke-torrent-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, *args):
        return subprocess.run(["php", str(FIXTURE), *args], capture_output=True, text=True, timeout=20)

    def test_tracker_free_and_announce_fixtures_share_one_info_dictionary(self):
        info = (b"d6:lengthi1e4:name15:service-tracker12:piece lengthi16384e6:pieces20:"
                + hashlib.sha1(b"x").digest() + b"e")
        expected_hash = hashlib.sha1(info).hexdigest().upper() + "\n"
        url = "http://127.0.0.1:19091/announce"
        for label, options, prefix in (("plain", [], b""),
                                       ("announce", ["--announce", url], b"8:announce%d:%s" % (len(url), url.encode()))):
            with self.subTest(label=label):
                source = self.root / label / "fixture.torrent"
                payload = self.root / label / "downloads"
                result = self.fixture(*options, "service-tracker", str(source), str(payload))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected_hash, ""))
                self.assertEqual(source.read_bytes(), b"d" + prefix + b"4:info" + info + b"e")
                self.assertEqual((payload / "service-tracker").read_bytes(), b"x")

    def test_invalid_usage_is_refused(self):
        for args in (["--announce"], ["--announce", "", "name", "source"], ["dir/name", "source"], ["only-name"]):
            with self.subTest(args=args):
                result = self.fixture(*args)
                self.assertEqual(result.returncode, 64)
                self.assertTrue(re.search(r"^Usage: torrent-fixture\.php \[--announce URL\]", result.stderr))


if __name__ == "__main__":
    unittest.main()
