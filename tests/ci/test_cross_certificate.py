"""Pin optional cross-certificate refusal and native-root selection branches."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CrossCertificateTests(unittest.TestCase):
    def test_native_root_expired_untrusted_and_valid_certificate_outcomes(self):
        source = (ROOT / "scripts/install-cross-certificate.sh").read_text()
        for scenario in ("native-root", "expired", "untrusted", "valid"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="docker-ca-contract-") as temporary:
                temporary = Path(temporary)
                fixtures = temporary / "bin"
                fixtures.mkdir()
                roots = temporary / "roots"
                roots.mkdir()
                certificate = temporary / "optional.crt"
                certificate.write_text("fixture certificate\n")
                if scenario == "native-root":
                    (roots / "native.crt").write_text("fixture native root\n")
                openssl = fixtures / "openssl"
                openssl.write_text('''#!/bin/sh
if [ "$1" = verify ]; then [ "$SCENARIO" != untrusted ]; exit; fi
path=$3
while [ "$#" -gt 0 ]; do
  case "$1" in
    -subject) printf 'subject=CN=fixture-root\\n'; exit 0 ;;
    -issuer) printf 'issuer=CN=fixture-root\\n'; exit 0 ;;
    -pubkey) printf 'fixture-public-key\\n'; exit 0 ;;
    -checkend) [ "$SCENARIO" != expired ]; exit ;;
  esac
  shift
done
exit 0
''')
                openssl.chmod(0o755)
                update = fixtures / "update-ca-certificates"
                update.write_text('#!/bin/sh\nprintf "updated\\n" > "$UPDATED"\n')
                update.chmod(0o755)
                script = temporary / "install.sh"
                script.write_text(source.replace("/usr/share/ca-certificates/mozilla", str(roots)))
                destination = temporary / "installed.crt"
                destination.write_text("old untrusted material\n")
                updated = temporary / "updated"
                result = subprocess.run(["sh", str(script), str(certificate), str(destination)], env={**os.environ, "PATH": str(fixtures) + os.pathsep + os.environ["PATH"], "SCENARIO": scenario, "UPDATED": str(updated)}, capture_output=True, text=True, timeout=5, check=False)
                if scenario == "untrusted":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(updated.exists())
                    self.assertEqual(destination.read_text(), "old untrusted material\n")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(updated.exists())
                    if scenario == "valid":
                        self.assertEqual(destination.read_bytes(), certificate.read_bytes())
                        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
                    else:
                        self.assertFalse(destination.exists())
                        self.assertIn("Skipping", result.stderr)


if __name__ == "__main__":
    unittest.main()
