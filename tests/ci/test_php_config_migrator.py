"""Quoted literals and assignment targets in the ruTorrent PHP config migrator.

The migrator reads operator configuration as tokens and never executes it. A
quoted key or path counts as proven only when it is decoded exactly as PHP
decodes it, and only a plain global variable is a managed assignment target.
The container cases in tests/update-config/php-cases.sh check the migrated
effective values; these host checks cover the decoder exhaustively.
"""

import json
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rutorrent-config.php"
# The helper's definitions end where its command-line entry point begins; the
# entry point checks image executables that a host does not have.
ENTRY = "\n$file = $argv[1] ?? '';\n"

PROBE = r"""<?php
declare(strict_types=1);
require $argv[1];
error_reporting(E_ALL & ~E_WARNING & ~E_COMPILE_WARNING); // Octal overflow is valid PHP.
$cases = json_decode(stream_get_contents(STDIN), true);
$literals = [];
foreach ($cases['literals'] as $literal) {
    $source = "<?php \$value = $literal;";
    try {
        token_get_all($source, TOKEN_PARSE);
    } catch (ParseError) {
        $literals[] = null;
        continue;
    }
    $token = source_tokens($source)[3];
    $decoded = literal_value($token);
    $literals[] = [$token['id'] === T_CONSTANT_ENCAPSED_STRING, $decoded === null ? null : bin2hex($decoded),
        bin2hex(eval("return $literal;"))]; // Trusted test literals only.
}
$targets = [];
foreach ($cases['sources'] as $source) {
    $targets[] = array_map(fn(array $found): array => [$found['variable'], $found['key']],
        assignments(source_tokens($source)));
}
echo json_encode(['literals' => $literals, 'targets' => $targets]);
"""


@requires_host_php("the PHP config migrator's literal decoder", extensions=("tokenizer", "json"))
class PhpConfigMigratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        source = MIGRATOR.read_text(encoding="utf-8")
        cls.functions = Path(cls.scratch.name) / "functions.php"
        cls.functions.write_text(source[:source.index(ENTRY)] + "\n", encoding="utf-8")
        cls.probe = Path(cls.scratch.name) / "probe.php"
        cls.probe.write_text(PROBE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def run_probe(self, literals=(), sources=()):
        result = subprocess.run(["php", str(self.probe), str(self.functions)], check=True, capture_output=True,
                                text=True, input=json.dumps({"literals": list(literals), "sources": list(sources)}))
        return json.loads(result.stdout)

    def assert_decoded_as_php(self, literals):
        """The decoded value of every constant literal equals PHP's; returns how many were constants."""
        compared = 0
        for literal, outcome in zip(literals, self.run_probe(literals=literals)["literals"]):
            if outcome is None or not outcome[0]:
                continue  # Not valid PHP, or interpolation rather than a constant.
            compared += 1
            self.assertEqual(outcome[1], outcome[2], literal)
        return compared

    def test_named_escapes(self):
        literals = [r'"\u{70}hp"', r'"/usr/bin/\php"', r'"\x63url"', r'"\143url"', r'"\560hp"', r'"\\u{70}"',
                    r'"\$x"', r'"a$"', r'"$1"', r'"\e\v\f\n\r\t"', r'"\u{0}\u{7F}\u{80}\u{7FF}\u{800}"',
                    r'"\u{FFFF}\u{10000}\u{1F600}\u{10FFFF}\u{0000070}"', r'"\x"', r'"\xg"', r'"\x4"', r'"\8\9"',
                    r'"\0\00\000\0000"', r'"\\\\"', r'"\{"', r'"\u"', r'"\u{70"', r'"\\\""', r"'\\'", r"'\''",
                    r"'\x41\n'", r"'a\\\b'", r'b"\x41"', r"B'a\'b'", r'b"php"']
        self.assertEqual(self.assert_decoded_as_php(literals), len(literals) - 1)  # "\u{70" is a parse error.

    def test_generated_escapes(self):
        generator = random.Random(20261018)
        double = ["\\", "\\\\", '\\"', "n", "x", "u", "{", "}", "0", "3", "7", "8", "f", "F", "g", "$", " ", "p",
                  "\\u{", "\\x", "\\0", "\\u{70}", "\\u{1F600}", "\\400", "\\e", "\\v", "\\$", "\\{"]
        single = ["\\", "\\\\", "\\'", "a", "n", "x"]
        literals = ['"' + "".join(generator.choices(double, k=generator.randint(1, 8))) + '"' for _ in range(3000)]
        literals += ["'" + "".join(generator.choices(single, k=generator.randint(1, 8))) + "'" for _ in range(500)]
        self.assertGreater(self.assert_decoded_as_php(literals), 1500)

    def test_managed_targets_are_plain_globals(self):
        sources = [
            "<?php Foo::$scgi_host = 1; $o->$scgi_host = 2; $$scgi_host = 3; fn() => $scgi_host = 4;"
            " static fn() => $a = $scgi_port = 5; $scgi_host = 6;",
            "<?php $pathToExternals['php'] = 'a'; $pathToExternals['php'] ??= 'b'; Foo::$pathToExternals['php'] = 'c';"
            " $pathToExternals[\"\\u{70}hp\"] ??= 'd'; $pathToExternals ??= []; $topDirectory ??= '/';",
            "<?php if (false) $scgi_host = 1; else $scgi_port = 2; $o = new stdClass; $o?->x;",
        ]
        self.assertEqual(self.run_probe(sources=sources)["targets"], [
            [["$scgi_host", None]],
            [["$pathToExternals", "php"], ["$pathToExternals", "php"], ["$pathToExternals", "php"]],
            [["$scgi_host", None], ["$scgi_port", None], ["$o", None]],
        ])

    def test_arrow_scope_stops_before_outer_conditional_branch(self):
        sources = [
            "<?php false ? fn() => 1 : $scgi_host = 2;",
            "<?php false ? fn(): int => 1 : $pathToExternals['php'] = 'a';",
            "<?php false ? fn() => fn(): int => $scgi_port = 1 : $scgi_host = 2;",
            "<?php fn() => false ? $scgi_host = 1 : $scgi_port = 2; $topDirectory = '/';",
        ]
        self.assertEqual(self.run_probe(sources=sources)["targets"], [
            [["$scgi_host", None]],
            [["$pathToExternals", "php"]],
            [["$scgi_host", None]],
            [["$topDirectory", None]],
        ])


if __name__ == "__main__":
    unittest.main()
