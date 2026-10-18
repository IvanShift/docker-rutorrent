"""Host checks of the shared smoke SCGI client, the torrent fixture generator
and the capacity smoke's in-container request fan-out.

A local Unix-socket peer stands in for the daemon, so whole-reply reading,
truncation, silence, faults and reply structure are exercised without a
container; a fake curl stands in for nginx and the held PHP workers.
"""

import hashlib
import re
import shutil
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
CAPACITY = ROOT / "tests/smoke/health-capacity.sh"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


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


# The header lines that both daemons send, in their order.
SCGI_HEADERS = (b"Status: 200 OK", b"Content-Type: text/xml", b"Content-Length: %d")


def scgi_reply(body, headers=SCGI_HEADERS):
    """BODY behind CRLF-ended header lines; a %d in a line takes the body length."""
    lines = (line % len(body) if b"%d" in line else line for line in headers)
    return b"".join(line + b"\r\n" for line in lines) + b"\r\n" + body


def xml_reply(value):
    return scgi_reply(('<?xml version="1.0"?><methodResponse><params><param><value><string>'
                       f"{value}</string></value></param></params></methodResponse>").encode())


def response(value):
    """A methodResponse around one <value> element, given as XML text."""
    return f'<?xml version="1.0"?><methodResponse><params><param>{value}</param></params></methodResponse>'.encode()


@requires_host_php("the smoke SCGI client", extensions=("SimpleXML",))
class ServiceRpcClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smoke-rpc-")
        self.addCleanup(self.temp.cleanup)
        self.socket_path = Path(self.temp.name) / "rtorrent.sock"
        self.requests = []

    def serve(self, respond, connections=1):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        server.listen(1)
        self.addCleanup(server.close)

        def run():
            for _ in range(connections):
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
        self.serve(lambda connection: connection.sendall(scgi_reply(fault)))
        result = self.call("d.name", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported target type found.", result.stderr)
        self.socket_path.unlink()
        result = self.call("system.pid")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"service-rpc: cannot connect to {self.socket_path} for system.pid", result.stderr)

    def serve_replies(self, replies):
        """Answer one connection per whole SCGI reply, in order."""
        pending = iter(replies)
        self.serve(lambda connection: connection.sendall(next(pending)), connections=len(replies))

    def serve_bodies(self, bodies):
        """Answer one connection per XML-RPC body, in order."""
        self.serve_replies([scgi_reply(body) for body in bodies])

    def test_every_scgi_header_line_is_checked(self):
        body = response("<value><i8>0</i8></value>")
        status, kind = b"Status: 200 OK", b"Content-Type: text/xml"
        wrong = b"Content-Length: %d" % (len(body) + 10)
        truncated = (f"service-rpc: truncated reply to session.save: Content-Length {len(body) + 10}, "
                     f"received {len(body)} bytes")
        single = "service-rpc: SCGI reply to session.save has no single numeric Content-Length header"
        malformed = "service-rpc: malformed SCGI header line in the reply to session.save:"
        cases = (
            # Content-Length is enforced wherever it stands and however it is spelled.
            ((status, kind, wrong), truncated),
            ((status, wrong, kind), truncated),
            ((wrong, status, kind), truncated),
            ((status, b"content-length:\t%d \t" % (len(body) + 10), kind), truncated),
            ((status, kind), single),
            ((status, b"Content-Length: %d", wrong, kind), single),
            ((status, b"Content-Length: %d", b"Content-Length: %d", kind), single),
            ((status, kind, b"Content-Length: "), single),
            ((status, kind, b"Content-Length: -1"), single),
            ((status, kind, b"Content-Length: 1 0"), single),
            ((status, b"Content-Length: +%d", kind), single),
            ((b"Status 200 OK", kind, b"Content-Length: %d"), f'{malformed} "Status 200 OK"'),
            ((status, b": text/xml", b"Content-Length: %d"), f'{malformed} ": text/xml"'),
            ((status, b"Content Type: text/xml", b"Content-Length: %d"), f'{malformed} "Content Type: text/xml"'),
            ((status, kind, b"Content-Length : %d"), f'{malformed} "Content-Length : {len(body)}"'),
            ((status, b"Content-Type: text/xml\nX-Test: lf", b"Content-Length: %d"),
             f'{malformed} "Content-Type: text/xml\\nX-Test: lf"'),
            ((status, b"Content-Type: text/\rxml", b"Content-Length: %d"), f'{malformed} "Content-Type: text/\\rxml"'),
            ((status, kind, b"X-Test: a\0b", b"Content-Length: %d"), f'{malformed} "X-Test: a\\000b"'),
        )
        self.serve_replies([scgi_reply(body, headers) for headers, _ in cases])
        for headers, message in cases:
            with self.subTest(headers=headers):
                result = self.call("session.save")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", message + "\n"))

    def test_correct_content_length_is_accepted_in_any_header_order(self):
        status, kind = b"Status: 200 OK", b"Content-Type: text/xml"
        cases = (
            SCGI_HEADERS,
            (b"Content-Length: %d", status, kind),
            (status, b"Content-Length: %d", kind),
            (b"content-length:%d",),
            (b"CONTENT-LENGTH: \t%d \t", status, kind),
        )
        body = response("<value><i8>0</i8></value>")
        self.serve_replies([scgi_reply(body, headers) for headers in cases])
        for headers in cases:
            with self.subTest(headers=headers):
                result = self.call("session.save")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "0\n", ""))

    def test_reply_without_one_xmlrpc_result_is_refused(self):
        prefix = "service-rpc: XML-RPC reply to session.save has"
        namespace = "service-rpc: unexpected reply to session.save: XML namespace"
        cases = (
            (b"<not-an-xmlrpc-response/>",
             "service-rpc: unexpected reply to session.save: root element <not-an-xmlrpc-response>, expected <methodResponse>"),
            (b'<?xml version="1.0"?><methodResponse/>',
             f"{prefix} no single result: <methodResponse> must hold exactly one <params>"),
            (b'<?xml version="1.0"?><methodResponse><params/></methodResponse>',
             f"{prefix} no single result: <params> must hold exactly one <param>"),
            (response("<value><i8>1</i8></value></param><param><value><i8>2</i8></value>"),
             f"{prefix} no single result: <params> must hold exactly one <param>"),
            (b'<?xml version="1.0"?><methodResponse><params><param/></params></methodResponse>',
             f"{prefix} no single result: <param> must hold exactly one <value>"),
            (response("<string>bare</string>"), f"{prefix} no single result: <param> must hold exactly one <value>"),
            (b'<?xml version="1.0"?><methodResponse><params><param><value><i8>0</i8></value></param></params>'
             b"<fault><value><string>both</string></value></fault></methodResponse>",
             f"{prefix} no single result: <methodResponse> must hold exactly one <params>"),
            (response("<value><string>a</string><string>b</string></value>"), f"{prefix} malformed <value> result"),
            (response("<value><i8></i8></value>"), f"{prefix} malformed <i8> result"),
            (response("<value><int>1.5</int></value>"), f"{prefix} malformed <int> result"),
            (response("<value><string><b>nested</b></string></value>"), f"{prefix} malformed <string> result"),
            (response("<value><array><data/></array></value>"), f"{prefix} unsupported result type <array>"),
            (response("<value><boolean>1</boolean></value>"), f"{prefix} unsupported result type <boolean>"),
            # No element or attribute may sit in a namespace, where an unqualified lookup misses it.
            (response('<value><x:array xmlns:x="urn:x"/></value>'), f'{namespace} "urn:x", expected none'),
            (b'<?xml version="1.0"?><methodResponse xmlns:x="urn:x"><params><param><value><i8>0</i8></value>'
             b"</param></params><x:fault><value><string>hidden</string></value></x:fault></methodResponse>",
             f'{namespace} "urn:x", expected none'),
            (response('<value><i8 xmlns="urn:x">0</i8></value>'), f'{namespace} "urn:x", expected none'),
            (b'<?xml version="1.0"?><x:methodResponse xmlns:x="urn:x"><x:params><x:param><x:value><x:i8>0</x:i8>'
             b"</x:value></x:param></x:params></x:methodResponse>", f'{namespace} "urn:x", expected none'),
            (b'<?xml version="1.0"?><methodResponse xmlns="urn:x"><params><param><value><i8>0</i8></value>'
             b"</param></params></methodResponse>", f'{namespace} "urn:x", expected none'),
            (response("<value><xml:i8>0</xml:i8></value>"), f'{namespace} "{XML_NAMESPACE}", expected none'),
            (response('<value xml:space="preserve"><i8>0</i8></value>'), f'{namespace} "{XML_NAMESPACE}", expected none'),
            # Text beside an element is not whitespace layout.
            (response("<value>junk<i8>0</i8>junk</value>"), f"{prefix} malformed <value> result"),
            (response("<value>junk<i8>0</i8></value>"), f"{prefix} malformed <value> result"),
            (response("<value><i8>0</i8>junk</value>"), f"{prefix} malformed <value> result"),
            (response("<value><![CDATA[junk]]><i8>0</i8></value>"), f"{prefix} malformed <value> result"),
            (b'<?xml version="1.0"?><methodResponse>junk<params><param><value><i8>0</i8></value></param></params>'
             b"</methodResponse>", f"{prefix} text outside its result in <methodResponse>"),
            (b'<?xml version="1.0"?><methodResponse><params><param><value><i8>0</i8></value></param>junk</params>'
             b"</methodResponse>", f"{prefix} text outside its result in <params>"),
            (response("<value><i8>0</i8></value>junk"), f"{prefix} text outside its result in <param>"),
        )
        self.serve_bodies([body for body, _ in cases])
        for body, message in cases:
            with self.subTest(body=body):
                result = self.call("session.save")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", message + "\n"))

    def test_present_results_including_empty_strings_are_printed(self):
        cases = (
            (response("<value><string></string></value>"), "\n"),
            (response("<value><string/></value>"), "\n"),
            # XML-RPC: a value without a type element is a string.
            (response("<value/>"), "\n"),
            (response("<value>untyped</value>"), "untyped\n"),
            (response("<value><i8>-3</i8></value>"), "-3\n"),
            (response("<value><i4>7</i4></value>"), "7\n"),
            (response("<value><int>+8</int></value>"), "+8\n"),
            # xmlrpc-c (legacy build) separates elements with CRLF.
            (b'<?xml version="1.0" encoding="UTF-8"?>\r\n<methodResponse>\r\n<params>\r\n'
             b"<param><value><i8>0</i8></value></param>\r\n</params>\r\n</methodResponse>\r\n", "0\n"),
            # Indentation around every element is layout, not text.
            (b'<?xml version="1.0"?>\n<methodResponse>\n  <params>\n\t<param>\n\t  <value>\n\t    <i8>5</i8>\n'
             b"\t  </value>\n\t</param>\n  </params>\n</methodResponse>\n", "5\n"),
            (response("<value> <string>s</string>\r\n</value>"), "s\n"),
            # The text of an untyped value is the string, spaces included.
            (response("<value>  two words </value>"), "  two words \n"),
        )
        self.serve_bodies([body for body, _ in cases])
        for body, output in cases:
            with self.subTest(body=body):
                result = self.call("session.save")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, output, ""))


# Stands in for nginx and busy.php: "released" once the test opens the barrier.
# Request 1 answers last, so the fan-out also waits on requests that ended first.
FAKE_CURL = r"""#!/bin/sh
for url; do :; done
id=${url##*id=}
printf '%s\n' "$id" >> "$CALLS"
[ "$id" != 1 ] || sleep 0.3
case " $FAIL_IDS " in *" $id "*) exit 22 ;; esac
case " $EARLY_IDS " in *" $id "*) printf barrier-timeout; exit 0 ;; esac
printf released
"""


def capacity_requests():
    """The in-container script of health-capacity.sh that holds the 15 regular workers."""
    scripts = re.findall(r"sh -eu -c '([^']*busy\.php\?id=[^']*)'", CAPACITY.read_text())
    assert len(scripts) == 1, scripts
    return scripts[0]


class CapacityRequestTests(unittest.TestCase):
    """Every held request must succeed and answer only after the release barrier."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="smoke-capacity-")
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        curl = self.work / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.calls = self.work / "calls"
        self.script = capacity_requests()

    def shells(self):
        # The container runs BusyBox ash; dash is the host /bin/sh on Ubuntu.
        yield ("sh",)
        if shutil.which("busybox"):
            yield ("busybox", "sh")

    def run_requests(self, shell, **variables):
        self.calls.write_text("")
        env = {"PATH": f"{self.work}:/usr/bin:/bin", "CALLS": str(self.calls), **variables}
        result = subprocess.run([*shell, "-eu", "-c", self.script], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(sorted(self.calls.read_text().split(), key=int), [str(i) for i in range(1, 16)])
        return result.returncode, result.stdout + result.stderr

    def test_released_requests_succeed(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                self.assertEqual(self.run_requests(shell), (0, ""))

    def test_any_failed_or_unreleased_request_fails_the_fan_out(self):
        every = " ".join(str(i) for i in range(1, 16))
        for shell in self.shells():
            for variables, messages in (
                ({"FAIL_IDS": every}, [f"request {i} failed with status 22" for i in range(1, 16)]),
                ({"FAIL_IDS": "1"}, ["request 1 failed with status 22"]),
                ({"FAIL_IDS": "7"}, ["request 7 failed with status 22"]),
                ({"EARLY_IDS": "3"}, ["request 3 answered barrier-timeout instead of released",
                                      "request 3 failed with status 1"]),
            ):
                with self.subTest(shell=shell, **variables):
                    status, output = self.run_requests(shell, **variables)
                    self.assertNotEqual(status, 0)
                    self.assertEqual(sorted(output.splitlines()), sorted(messages))


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
