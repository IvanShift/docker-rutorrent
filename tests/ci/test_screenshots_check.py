"""The media gate must reject stale, absent, partial and wrong-content results."""

import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / 'tests/smoke/screenshots-check.php'
FIXTURES = ROOT / 'tests/fixtures/media'


@requires_host_php('screenshot smoke negative controls', extensions=('zlib',))
class ScreenshotAssertionTests(unittest.TestCase):
    def php(self, source, *arguments, env=None):
        return subprocess.run(['php', '-r', 'require $argv[1]; '+source, str(CHECK), *map(str, arguments)],
                              capture_output=True, text=True, timeout=30, env=env)

    def test_renderer_rejects_stale_frame_before_calling_tool(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / 'frame.png'
            frame.write_bytes(b'earlier valid frame')
            result = self.php('mediaRender(["touch", $argv[3]], $argv[2]);', frame, Path(root) / 'called')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already exists', result.stderr)
            self.assertFalse((Path(root) / 'called').exists())
            self.assertEqual(frame.read_bytes(), b'earlier valid frame')

    def test_renderer_rejects_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / 'frame.png'
            frame.symlink_to('absent')
            result = self.php('mediaRender(["true"], $argv[2]);', frame)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already exists', result.stderr)
            self.assertTrue(frame.is_symlink())

    def test_success_without_frame_or_with_empty_frame_is_rejected(self):
        for command in ('["true"]', '["touch", $argv[2]]'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as root:
                result = self.php('mediaRender('+command+', $argv[2]);', Path(root) / 'frame.png')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('No fresh nonempty frame', result.stderr)

    def test_command_failure_or_missing_decoder_cannot_pass(self):
        with tempfile.TemporaryDirectory() as root:
            tool = Path(root) / 'ffmpeg'
            tool.write_text('#!/bin/sh\nprintf "decoder unavailable\\n" >&2\nexit 8\n')
            tool.chmod(0o755)
            result = self.php('mediaRender(["ffmpeg"], $argv[2]);', Path(root) / 'frame.png',
                              env={**os.environ, 'PATH': root+os.pathsep+os.environ['PATH']})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Media command failed with status 8', result.stderr)
            self.assertIn('decoder unavailable', result.stderr)

    def test_pixels_reject_blank_changed_and_truncated_frames(self):
        setup = '$rgb = gzdecode(file_get_contents($argv[2])); '
        reference = FIXTURES / 'reference/h264-first.rgb.gz'
        valid = self.php(setup+'mediaCheckRgb($rgb, $rgb, 96, 36, 1.0);', reference)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        for mutation, message in [('str_repeat("\\0", strlen($rgb))', 'Blank'),
                                  ('$rgb ^ str_repeat("\\xff", strlen($rgb))', 'Frame pixels differ'),
                                  ('substr($rgb, 1)', 'Wrong RGB frame length')]:
            with self.subTest(mutation=mutation):
                result = self.php(setup+'mediaCheckRgb('+mutation+', $rgb, 96, 36, 1.0);', reference)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_dimensions_format_and_ignored_seek_are_rejected(self):
        # A valid minimal PNG header is enough: the wrong dimensions/type must
        # be rejected before the decoder is invoked.
        with tempfile.TemporaryDirectory() as root:
            png = Path(root) / 'wrong.png'
            png.write_bytes(b'\x89PNG\r\n\x1a\n'+struct.pack('>I4sIIBBBBB', 13, b'IHDR', 32, 32, 8, 2, 0, 0, 0)+b'\0'*4)
            for width, height, kind in ((96, 36, 3), (32, 32, 2)):
                result = self.php(f'mediaCheckFrame($argv[2], {width}, {height}, {kind}, "", 1.0);', png)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Wrong frame dimensions or image format', result.stderr)
        result = self.php('$rgb = gzdecode(file_get_contents($argv[2])); mediaCheckSeek($rgb, $rgb);',
                          FIXTURES / 'reference/h264-first.rgb.gz')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Seek did not change', result.stderr)

    def test_rgb_transport_accepts_pixels_and_rejects_partial_or_wrong_png(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))

        header = chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 2, 0, 0, 0))
        rows = b'\0\xff\0\0\0\xff\0'
        prefix = b'\x89PNG\r\n\x1a\n'+header
        ending = chunk(b'IEND', b'')
        valid = prefix+chunk(b'IDAT', zlib.compress(rows))+ending
        with tempfile.TemporaryDirectory() as root:
            png = Path(root) / 'rgb.png'
            png.write_bytes(valid)
            result = self.php('echo bin2hex(mediaPngRgb(file_get_contents($argv[2]), 2, 1));', png)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'ff000000ff00')
            for name, value in [('absent', b''), ('partial', valid[:-1]),
                                ('wrong_rows', prefix+chunk(b'IDAT', zlib.compress(rows[:-1]))+ending),
                                ('predictor', prefix+chunk(b'IDAT', zlib.compress(b'\1'+rows[1:]))+ending)]:
                with self.subTest(name=name):
                    png.write_bytes(value)
                    result = self.php('mediaPngRgb(file_get_contents($argv[2]), 2, 1);', png)
                    self.assertNotEqual(result.returncode, 0)

    def test_exact_261_word_fingerprints_are_load_bearing(self):
        expected = json.loads((FIXTURES / 'fingerprints.json').read_text())
        for extension, value in expected.items():
            self.assertEqual(len(value['fingerprint']), 261)
            self.assertGreater(len(set(value['fingerprint'])), 250)
            source = '$expected = json_decode($argv[2], true); mediaCheckFingerprint($argv[3], $expected);'
            valid = self.php(source, json.dumps(value), json.dumps(value))
            self.assertEqual(valid.returncode, 0, valid.stderr)
            for mutation in ('word', 'duration', 'partial'):
                with self.subTest(extension=extension, mutation=mutation):
                    changed = json.loads(json.dumps(value))
                    if mutation == 'word':
                        changed['fingerprint'][17] ^= 1
                    elif mutation == 'duration':
                        changed['duration'] -= 1
                    else:
                        changed['fingerprint'].pop()
                    result = self.php(source, json.dumps(value), json.dumps(changed))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('differs from the reviewed reference', result.stderr)


if __name__ == '__main__':
    unittest.main()
