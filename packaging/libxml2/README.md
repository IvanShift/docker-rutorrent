# libxml2 security backport

This package starts from Alpine v3.24 `main/libxml2` 2.13.9-r2 and applies the official upstream fixes missing from that package. Keep `libxml2.so.2`: PHP 8.5 and FFmpeg in Alpine v3.24 link to it. libxml2 2.14 changed the SONAME to `libxml2.so.16`, and 2.15 also removed the built-in HTTP client and LZMA support. Replacing the library with 2.15.4 would require a coordinated consumer rebuild and feature review.

The source archive retains Alpine's SHA512. Every patch has its own checksum in `APKBUILD`. The original Alpine feature flags, split packages and `make runtests` check are retained. Only the runtime `libxml2` APK is needed in the final image. Remove the local backport when Alpine publishes a package containing these fixes.

The `CVE-2026-86137` through `CVE-2026-86144` patches come from the [upstream 2.15.4 changes](https://github.com/GNOME/libxml2/compare/v2.15.3...v2.15.4). The existing Alpine `CVE-2026-6732` fix and its official regression are preserved. The 86141 patch corrects an allocation-failure path introduced by 86137 and must accompany it.

Backport adaptations are limited to the old source layout and APIs:

- 6732 is rebased to the 2.13 parser context so it applies without fuzz.
- 86139 and 86142 retain the old allocator call in patch context.
- 86143 guards both separate 2.13 output loops before narrowing lengths to the callback's `int`; the upstream implementation uses one shared loop. The flush checks follow upstream.
- 86144 sets parse options before the old external-entity loader and keeps the existing public typedefs. Its upstream XML fixtures are unchanged. Separate expected diagnostics preserve the 2.13 warning wording and the second legacy generic error from the global-handler entrypoint.

The `old-*` series supplies the earlier catalog, RelaxNG, shell and Python fixes listed in its patch headers. Catalog followups travel with their original fixes; the unrelated newer catalog cache is not introduced. The newer standalone catalog test harness requires an API absent from 2.13 and is not grafted into this compatibility package. Existing upstream tests and the applicable official regression fixtures remain enabled.

RelaxNG include nesting is limited to 1000 by default. Operators needing a larger legitimate schema can set `RNG_INCLUDE_LIMIT` to a positive integer; an empty value or `0` keeps the default, and an invalid numeric value refuses the parse with an error.

`CVE-2026-6653` and `CVE-2025-9714` were already fixed before 2.13.9. Command-line shell and Python-binding fixes cover the corresponding build splits; those splits are not installed in the final container. A scanner that matches only the upstream version string may still report fixed CVEs: this package intentionally retains the ABI-compatible 2.13.9 version and records the fixes under package revision r3.
