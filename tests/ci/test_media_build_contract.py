"""Exercise the APK recipes' configure calls without compiling their sources."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MediaBuildContract(unittest.TestCase):
    def recipe_environment(self):
        return dict(os.environ, CARCH="x86_64", FFMPEG_VERSION="9.0.2",
                    FFMPEG_SHA512="checked-by-abuild",
                    CHROMAPRINT_VERSION="1.6.2",
                    CHROMAPRINT_SHA512="checked-by-abuild", srcdir="/unused",
                    CC="/bin/true", CFLAGS="", LDFLAGS="")

    def recipe_dependencies(self, name):
        recipe = ROOT / "packaging" / name / "APKBUILD"
        self.assertTrue(recipe.is_file(), f"missing source recipe for {name}")
        result = subprocess.run(["sh", "-e", "-c", '. "$1"; printf "%s\\n" "$makedepends"',
                                 "recipe", str(recipe)], env=self.recipe_environment(),
                                text=True, capture_output=True, check=True)
        return shlex.split(result.stdout)

    def configure_arguments(self, name, command):
        recipe = ROOT / "packaging" / name / "APKBUILD"
        self.assertTrue(recipe.is_file(), f"missing source recipe for {name}")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "tools").mkdir()
            (base / "bin").mkdir()
            collector = '#!/bin/sh\nprintf "%s\\n" "$@" >> "$RECIPE_CALLS"\nprintf "%s\\n" "---end---" >> "$RECIPE_CALLS"\n'
            target = base / ("configure" if command == "configure" else "bin/cmake")
            target.write_text(collector)
            target.chmod(0o755)
            make = base / "bin/make"
            make.write_text("#!/bin/sh\nexit 0\n")
            make.chmod(0o755)
            calls = base / "calls"
            env = dict(self.recipe_environment(), RECIPE_CALLS=str(calls),
                       PATH=str(base / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["sh", "-e", "-c", '. "$1"; build',
                                     "recipe", str(recipe)], cwd=base, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return calls.read_text().split("---end---\n", 1)[0].splitlines()

    def test_ffmpeg_keeps_local_media_and_excludes_network_devices_and_hardware(self):
        args = self.configure_arguments("ffmpeg", "configure")
        for feature in ("autodetect", "network", "devices", "hwaccels", "ffplay", "static"):
            self.assertIn("--disable-" + feature, args)
        for feature in ("libdav1d", "libjxl", "libmp3lame", "shared", "zlib", "bzlib"):
            self.assertIn("--enable-" + feature, args)
        self.assertIn("--enable-indev=lavfi", args)
        self.assertIn("--disable-encoders", args)
        encoders = next(arg.split("=", 1)[1].split(",") for arg in args
                        if arg.startswith("--enable-encoder="))
        self.assertTrue({"mjpeg", "png", "ffv1", "pcm_s16le", "mpeg4", "aac", "libmp3lame"}
                        <= set(encoders))
        self.assertTrue(set(encoders).isdisjoint({"libvpx", "libvpx-vp9", "libx264", "libx265"}))
        filters = next(arg.split("=", 1)[1].split(",") for arg in args
                       if arg.startswith("--enable-filter="))
        self.assertTrue({"scale", "setsar", "aresample", "testsrc", "testsrc2", "sine"}
                        <= set(filters))

    def test_ffmpeg_needs_only_the_retained_external_media_backends(self):
        dependencies = set(self.recipe_dependencies("ffmpeg"))
        self.assertTrue({"dav1d-dev", "libjxl-dev", "lame-dev"} <= dependencies)
        removed = {"libvpx-dev", "x264-dev", "x265-dev", "libass-dev", "libdrm-dev",
                   "fontconfig-dev", "freetype-dev", "libplacebo-dev", "shaderc-dev",
                   "libxml2-dev", "openssl-dev", "vulkan-loader-dev", "v4l-utils-dev"}
        self.assertTrue({item.split("=", 1)[0] for item in dependencies}.isdisjoint(removed))

    def test_chromaprint_builds_fpcalc_against_the_selected_ffmpeg_abi(self):
        dependencies = self.recipe_dependencies("chromaprint")
        self.assertIn("ffmpeg-dev=9.0.2-r0", dependencies)
        args = self.configure_arguments("chromaprint", "cmake")
        self.assertIn("-DBUILD_TOOLS=ON", args)
        self.assertIn("-DFFT_LIB=fftw3", args)


if __name__ == "__main__":
    unittest.main()
