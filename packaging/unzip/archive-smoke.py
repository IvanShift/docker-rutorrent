#!/usr/bin/env python3
"""Exercise ordinary ZIP compatibility of the newly built UnZip executable."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} UNZIP")
    unzip = str(Path(sys.argv[1]).resolve(strict=True))
    environment = dict(os.environ, LC_ALL="C.UTF-8")
    # UnZip reads these as default command-line arguments. Packaging checks
    # must exercise the explicit test options, independent of the host shell.
    for name in ("UNZIP", "UNZIPOPT", "ZIPINFO", "ZIPINFOOPT"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="unzip-archive-smoke-") as temporary:
        work = Path(temporary)
        payloads = {
            "stored.txt": b"stored archive member\n",
            "nested/deflated.txt": b"ordinary compressed content\n" * 256,
            "nested/utf8-данные.txt": "UTF-8 filename and content: данные\n".encode(),
            "zip64.txt": b"small member with a valid ZIP64 local header\n",
            "empty.txt": b"",
        }
        archive = work / "valid.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name, payload in payloads.items():
                member = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED if "deflated" in name else zipfile.ZIP_STORED
                with output.open(member, "w", force_zip64=name == "zip64.txt") as handle:
                    handle.write(payload)

        def run(*arguments):
            return subprocess.run([unzip, *map(str, arguments)], cwd=work, env=environment,
                                  capture_output=True, check=True, timeout=10).stdout

        run("-t", archive)
        names = run("-Z1", archive).decode("utf-8").splitlines()
        if names != list(payloads):
            raise AssertionError(f"archive listing differs: {names!r}")
        destination = work / "extracted"
        run("-o", archive, "-d", destination)
        extracted = {path.relative_to(destination).as_posix(): path.read_bytes()
                     for path in destination.rglob("*") if path.is_file()}
        if extracted != payloads:
            raise AssertionError("extracted filenames or contents differ")
    print("ok - UnZip stored, deflated, UTF-8, ZIP64 and empty members")


if __name__ == "__main__":
    main()
