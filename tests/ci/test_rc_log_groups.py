"""Log group names of the literal rc migration.

libtorrent a755a7a replaced its cascading log groups, and rTorrent refuses to
start on an unknown `log.add_output` group. The name lists below are
option_list_log_group in src/torrent/utils/option_strings.cc at the libtorrent
commits named; they are copied from the sources, not from documentation.
tests/update-config pins the same rules through the real update-config wrapper.
"""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
FIXTURES = ROOT / "tests/update-config/fixtures"

# libtorrent 8f042ad68ee06e2d92d7f9f6e7df5ef5ccb312fb, the previous native pin.
PIN_GROUPS = """
critical error warn notice info debug storage_critical storage_error storage_warn storage_notice
storage_info storage_debug __non_cascading__ connection connection_bind connection_fd
connection_filter connection_handshake connection_listen dht dht_all dht_error dht_controller
dht_node dht_router dht_server instrumentation_memory instrumentation_mincore
instrumentation_choke instrumentation_polling instrumentation_transfers mock_calls net_dns
net_http net_socket peer_choke_queue peer_list_events peer_list_address protocol_piece_events
protocol_metadata_events protocol_network_errors protocol_storage_errors resume_data rpc_events
rpc_dump session_events storage system system_poll system_thread torrent_debug torrent_events
tracker_dump tracker_events tracker_requests ui_events
""".split()
# libtorrent a755a7ae5fc3b25860198c5ca620f8455e114376; unchanged through the
# current native pin v0.16.26 21daa3b96a3b8cfe14961545b5115de7ed12da95.
MASTER_GROUPS = """
errors events debug_trace connection connection_bind connection_fd connection_filter
connection_handshake connection_listen dht dht_all dht_error dht_controller dht_node dht_router
dht_server instrumentation_memory instrumentation_mincore instrumentation_choke
instrumentation_polling instrumentation_transfers mock_calls net_dns net_http net_socket
peer_choke_queue peer_list_events peer_list_address protocol_piece_events
protocol_metadata_events protocol_network_errors protocol_storage_errors resume_data rpc_events
rpc_dump session_events storage_debug storage_events storage_errors system system_poll
system_thread torrent_debug torrent_events tracker_dump tracker_events tracker_requests
ui_events
""".split()
# libtorrent 756f70010779927dc0691e1e722ed433d5d295e1 (v0.13.8), used by legacy rTorrent 0.9.8.
LEGACY_GROUPS = """
critical error warn notice info debug connection_critical connection_error connection_warn
connection_notice connection_info connection_debug dht_critical dht_error dht_warn dht_notice
dht_info dht_debug peer_critical peer_error peer_warn peer_notice peer_info peer_debug
socket_critical socket_error socket_warn socket_notice socket_info socket_debug storage_critical
storage_error storage_warn storage_notice storage_info storage_debug thread_critical
thread_error thread_warn thread_notice thread_info thread_debug tracker_critical tracker_error
tracker_warn tracker_notice tracker_info tracker_debug torrent_critical torrent_error
torrent_warn torrent_notice torrent_info torrent_debug __non_cascading__ dht_all dht_manager
dht_node dht_router dht_server instrumentation_memory instrumentation_mincore
instrumentation_choke instrumentation_polling instrumentation_transfers peer_list_events
protocol_piece_events protocol_metadata_events protocol_network_errors protocol_storage_errors
resume_data rpc_events rpc_dump ui_events
""".split()

# What each removed group's cascade delivered at the previous pin (log.cc
# LOG_CASCADE and LOG_CHILDREN_CASCADE), expressed in the new groups.
NATIVE = {
    "critical": ["errors"], "error": ["errors"],
    "warn": ["errors", "storage_errors"], "notice": ["errors", "storage_errors"],
    "info": ["errors", "events", "storage_errors"],
    "debug": ["errors", "events", "debug_trace", "storage_errors", "storage_events"],
    "storage_critical": ["storage_errors"], "storage_error": ["storage_errors"],
    "storage_warn": ["storage_errors"], "storage_notice": ["storage_errors"],
    "storage_info": ["storage_errors", "storage_events"], "storage": ["storage_errors"],
    "__non_cascading__": [],
}
LEGACY = {
    "errors": ["error"], "events": ["info"], "debug_trace": ["debug"],
    "storage_errors": ["storage_error"], "storage_events": ["storage_info"],
}
ADD_OUTPUT = re.compile(r'^[ \t]*log\.add_output[ \t]*=[ \t]*"?([^",\s]+)"?[ \t]*,', re.M)


def groups(text):
    return ADD_OUTPUT.findall(text)


def add_output_lines(text):
    return [line for line in text.splitlines() if "log.add_output" in line]


class ShippedLogGroupTests(unittest.TestCase):
    def test_the_lists_differ_only_in_the_mapped_names(self):
        self.assertEqual(set(NATIVE), set(PIN_GROUPS) - set(MASTER_GROUPS))
        self.assertEqual(set(LEGACY), set(MASTER_GROUPS) - set(PIN_GROUPS))
        self.assertLessEqual({group for targets in NATIVE.values() for group in targets}, set(MASTER_GROUPS))
        self.assertLessEqual({group for targets in LEGACY.values() for group in targets}, set(LEGACY_GROUPS))

    def test_rc_files_loaded_without_migration_name_their_daemon_groups(self):
        # The template is migrated for 0.9.8 images; these files are not.
        for path, valid in (("rootfs/home/torrent/.rtorrent.rc", MASTER_GROUPS),
                            ("tests/smoke/fixtures/service-schedule/rtorrent.rc", MASTER_GROUPS),
                            ("tests/smoke/legacy-working.rc", LEGACY_GROUPS)):
            with self.subTest(path=path):
                found = groups((ROOT / path).read_text())
                self.assertTrue(found)
                self.assertLessEqual(set(found), set(valid))
        self.assertEqual(groups((ROOT / "rootfs/home/torrent/.rtorrent.rc").read_text()), ["errors"])


@requires_host_php("literal rc migration")
class LogGroupMigrationTests(unittest.TestCase):
    def migrated(self, content, mode):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_bytes(content.encode())
            subprocess.run(["php", str(MIGRATOR), str(path), "test.rc", mode],
                           check=True, text=True, capture_output=True)
            first = path.read_bytes().decode()
            subprocess.run(["php", str(MIGRATOR), str(path), "test.rc", mode],
                           check=True, text=True, capture_output=True)
            self.assertEqual(path.read_bytes().decode(), first, "second migration must be byte-identical")
            return first

    def test_every_removed_group_becomes_its_current_groups(self):
        for old, new in NATIVE.items():
            for quote in ('"', ""):
                with self.subTest(group=old, quote=quote):
                    migrated = self.migrated(f'log.add_output = {quote}{old}{quote}, "custom"\n', "native")
                    self.assertEqual(groups(migrated), new)
                    for line in add_output_lines(migrated):
                        self.assertTrue(line.endswith(f'{quote}, "custom"'), line)

    def test_every_new_group_returns_to_its_legacy_group(self):
        for new, old in LEGACY.items():
            with self.subTest(group=new):
                self.assertEqual(groups(self.migrated(f'log.add_output = "{new}", "custom"\n', "legacy")), old)

    def test_groups_valid_on_the_target_daemon_stay_as_written(self):
        for mode, keep in (("native", sorted(set(PIN_GROUPS) & set(MASTER_GROUPS))),
                           ("legacy", sorted(set(MASTER_GROUPS) - set(LEGACY)) + list(NATIVE))):
            for group in keep:
                with self.subTest(mode=mode, group=group):
                    line = f'log.add_output = "{group}", "custom"'
                    self.assertEqual(add_output_lines(self.migrated(line + "\n", mode)), [line])

    def test_only_an_exact_first_argument_is_renamed(self):
        # "errors" contains "error": a substring rename would corrupt it.
        lines = {
            "native": ['log.add_output = "errors", "error"', "log.add_output = error_custom, x",
                       'log.add_output = "error"x, "y"', "log.add_output = xerror, y",
                       'log.add_output = "Error", "y"', 'log.add_output = (cat,"err","or"), "y"',
                       'log.add_output = "err\\or", "y"', 'log.open_file = "error", "/tmp/error.log"',
                       '# log.add_output = "error", "y"', "# do:log.add_output=error,y",
                       'method.insert = d.log_error,simple,"log.add_output=error,y"'],
            "legacy": ['log.add_output = "error", "errors"', "log.add_output = errors_custom, x",
                       '#log.add_output = "errors", "y"', 'log.open_file = "errors", "/tmp/errors.log"'],
        }
        for mode, cases in lines.items():
            for line in cases:
                with self.subTest(mode=mode, line=line):
                    migrated = self.migrated(line + "\n", mode)
                    self.assertIn(line, migrated.splitlines())

    def test_indent_quotes_and_output_text_survive(self):
        # A CRLF line ending becomes LF, which rTorrent requires (test_rc_line_endings).
        migrated = self.migrated('  log.add_output="info" ,\trtorrent \r\n', "native")
        for group in ("errors", "events", "storage_errors"):
            self.assertIn(f'  log.add_output = "{group}" ,\trtorrent \n', migrated)
        # The daemon rejects trailing text, but the rename still leaves it alone.
        migrated = self.migrated('log.add_output = "warn", "rtorrent" # note\n', "native")
        self.assertEqual(add_output_lines(migrated), ['log.add_output = "errors", "rtorrent" # note',
                                                      'log.add_output = "storage_errors", "rtorrent" # note'])
        migrated = self.migrated("log.add_output = errors,rtorrent\r\n", "legacy")
        self.assertIn("log.add_output = error,rtorrent\n", migrated)

    def test_shipped_critical_default_is_removed_in_both_modes(self):
        for mode in ("native", "legacy"):
            with self.subTest(mode=mode):
                migrated = self.migrated('log.add_output = "critical", "rtorrent"\n'
                                         'log.add_output = "critical", "custom"\n', mode)
                self.assertEqual(groups(migrated), ["errors" if mode == "native" else "critical"])
                self.assertNotIn('"rtorrent"', migrated)

    def test_an_expansion_skips_only_groups_it_already_sent_to_that_output(self):
        migrated = self.migrated('log.add_output = "error", "rtorrent"\n'
                                 'log.add_output = "warn", rtorrent\n'
                                 'log.add_output = "info", "rtorrent"\n'
                                 'log.add_output = "info", "other"\n', "native")
        self.assertEqual(add_output_lines(migrated), [
            'log.add_output = "errors", "rtorrent"', 'log.add_output = "storage_errors", rtorrent',
            'log.add_output = "events", "rtorrent"', 'log.add_output = "errors", "other"',
            'log.add_output = "events", "other"', 'log.add_output = "storage_errors", "other"'])
        # Lines that need no rename are operator configuration, repeated or not.
        repeated = 'log.add_output = "errors", "rtorrent"\n' * 2
        self.assertEqual(groups(self.migrated(repeated, "native")), ["errors", "errors"])

    def test_a_closed_output_is_subscribed_again_after_reopening(self):
        # libtorrent's log.close clears the output from every group, and a
        # reopened output starts without groups (log.cc log_close_output).
        migrated = self.migrated((FIXTURES / "log-close-reopen.input.rc").read_text(), "native")
        self.assertEqual(migrated, (FIXTURES / "log-close-reopen.expected.rc").read_text())

    def test_subscriptions_are_shared_until_a_command_can_close_the_output(self):
        # Only a listed daemon command keeps the subscriptions: a definition
        # that runs later, a schedule, an external launch or a setting closes
        # nothing while rTorrent reads the rc, and no rc can redefine a daemon
        # command. Any other name can be an operator method, however defined
        # (method.insert.*, method.redirect, an import) and wherever; a call to
        # it can close the output, and so can log.close, a reopen, an import,
        # control flow and a parse-time ( or $ evaluation. Definitions come
        # before the first subscription, so only the call sits between the two.
        for before, between, shared in (
                ("", "", True), ("", "\n", True), ("", "# note\n", True),
                ("", 'system.method.insert = smoke.migrated.simple,simple,"execute2=echo,native-migration-ok"\n', True),
                ("", 'schedule2 = later,0,60,"log.close=rtorrent"\n', True),
                ("", 'method.set_key = event.download.finished,later,"log.close=rtorrent"\n', True),
                ("", "system.daemon.set = true\nport_range = 45000-45000\nnetwork.scgi.open_local = /run/x.sock\n", True),
                ("", "execute.nothrow.bg = {sh,-c,true}\n", True),
                ("", 'method.insert.simple = later,"log.close=rtorrent"\nmethod.insert.value = later.count,0\n'
                     'method.redirect = later.close,log.close\nmethod.set = later,"log.close=rtorrent"\n', True),
                ("", "throttle.global_down.max_rate.set_kb = 100\ntrackers.numwant.set = 50\ndht.mode.set = auto\n", True),
                ("", "log.close = rtorrent\n", False), ("", "import = /config/operator.rc\n", False),
                ("", "try_import = /config/operator.rc\n", False),
                ("", 'log.open_file = "rtorrent", "/tmp/other.log"\n', False),
                ("", 'branch = 1, "log.close=rtorrent"\n', False), ("", "operator.logs =\n", False),
                ('method.insert = logs.set,simple,"log.close=rtorrent"\n', "logs.set = 1\n", False),
                ('method.insert.simple = logs.set,"log.close=rtorrent"\n', "logs.set =\n", False),
                ('method.insert.c_simple = logs.set,"log.close=rtorrent"\n', "logs.set =\n", False),
                ('method.insert.s_c_simple = logs.set,"log.close=rtorrent"\n', "logs.set =\n", False),
                ("method.redirect = logs.set,log.close\n", "logs.set = rtorrent\n", False),
                ('method.insert.simple = logs,"print=x"\n', 'method.set = logs,"log.close=rtorrent"\nlogs =\n', False),
                ("method.insert.value = logs.limit,0\n", "logs.limit.set = 1\n", False),
                ("import = /config/operator.rc\n", "logs.set = 1\n", False), ("", "operator.flag.set = 1\n", False),
                ("", "method.insert = cfg.closed,string|const,(log.close,rtorrent)\n", False),
                ("", 'method.set_key = event.download.finished,x,"$log.close=rtorrent"\n', False),
                ("", 'log.add_output = "error", (cat,rtorrent)\n', False)):
            with self.subTest(before=before, between=between):
                migrated = self.migrated(before + 'log.add_output = "error", "rtorrent"\n' + between
                                         + 'log.add_output = "warn", "rtorrent"\n', "native")
                warn = ['log.add_output = "storage_errors", "rtorrent"']
                if not shared:
                    warn.insert(0, 'log.add_output = "errors", "rtorrent"')
                self.assertEqual(add_output_lines(migrated)[-len(warn):], warn)
                self.assertEqual(len([line for line in add_output_lines(migrated) if '"rtorrent"' in line]),
                                 1 + len(warn))

    def test_legacy_subscriptions_are_shared_only_across_its_daemon_commands(self):
        # 0.9.8 names: schedule2, port_range and execute2 are its commands; an
        # operator method is not. A repeated renamed group shows the difference.
        for before, between, shared in (
                ("", "", True), ("", 'schedule2 = later,0,60,"log.close=rtorrent"\n', True),
                ("", "port_range = 45000-45000\nexecute2 = {sh,-c,true}\n", True),
                ("", "network.listen.port.range.set = 45000-45000\n", True),
                ('method.insert.simple = logs.set,"log.close=rtorrent"\n', "logs.set =\n", False),
                ("method.redirect = logs.set,log.close\n", "logs.set = rtorrent\n", False),
                ("", "log.close = rtorrent\n", False)):
            with self.subTest(before=before, between=between):
                migrated = self.migrated(before + 'log.add_output = "errors", "rtorrent"\n' + between
                                         + 'log.add_output = "errors", "rtorrent"\n', "legacy")
                self.assertEqual(add_output_lines(migrated),
                                 ['log.add_output = "error", "rtorrent"'] * (1 if shared else 2))

    def test_an_operator_method_that_reopens_the_output_keeps_the_next_subscription(self):
        # The re-review example: logs.set closes and reopens the output, which
        # drops its groups, so the second subscription is needed. The fixture
        # also calls a method.redirect alias of log.close.
        migrated = self.migrated((FIXTURES / "log-method-simple.input.rc").read_text(), "native")
        self.assertEqual(migrated, (FIXTURES / "log-method-simple.expected.rc").read_text())
        self.assertEqual(add_output_lines(migrated)[:2], ["log.add_output = errors,operator"] * 2)

    def test_historical_template_lines_keep_one_subscription_per_group(self):
        # Mirrors tests/smoke/rtorrent-log-config-migration.sh: a migrated fresh
        # template turned back into historical spellings, then two operator lines.
        values = {"<DOWNLOAD_DIRECTORY>": "/data/downloads", "<MODE_DHT>": "auto", "<PORT_DHT>": "6881",
                  "<PEER_EXCHANGE>": "yes", "<PORT_RTORRENT>": "45000", "<BLOCK_IPV6>": "no"}
        template = (ROOT / "rootfs/home/torrent/.rtorrent.rc").read_text()
        for placeholder, value in values.items():
            template = template.replace(placeholder, value)
        historical = self.migrated(template, "native")
        for old, new in (("network.listen.port.range.set", "port_range"), ("network.listen.port.random.set", "port_random"),
                         ("dht.override_port.set", "dht_port"),
                         ("protocol.encryption.set = allow", "encryption = allow_incoming,try_outgoing,enable_retry"),
                         ("method.set_key", "system.method.set_key"), ("execute.nothrow=", "execute2="),
                         ("execute.nothrow =", "execute2 =")):
            historical = historical.replace(old, new)
        historical = historical.replace('log.add_output = "errors", "rtorrent"\n', 'log.add_output = "error", "rtorrent"\n')
        historical += ('system.method.insert = smoke.migrated.simple,simple,"execute2=echo,native-migration-ok"\n'
                       'log.add_output = "info", "rtorrent"\n')
        migrated = self.migrated(historical, "native")
        self.assertIn('method.insert = smoke.migrated.simple,simple,"execute.nothrow=echo,native-migration-ok"',
                      migrated)
        self.assertEqual(add_output_lines(migrated), ['log.add_output = "errors", "rtorrent"',
                                                      'log.add_output = "events", "rtorrent"',
                                                      'log.add_output = "storage_errors", "rtorrent"'])

    def test_a_renamed_block_stays_the_same_across_mode_switches(self):
        # Sharing within the block keeps each native/legacy switch from adding lines.
        native = self.migrated('log.add_output = "info", "rtorrent"\n', "native")
        legacy = self.migrated(native, "legacy")
        self.assertEqual(add_output_lines(legacy), ['log.add_output = "error", "rtorrent"',
                                                    'log.add_output = "info", "rtorrent"',
                                                    'log.add_output = "storage_error", "rtorrent"'])
        self.assertEqual(self.migrated(legacy, "native"), native)

    def test_image_default_survives_a_native_legacy_round_trip(self):
        line = 'log.add_output = "error", "rtorrent"\n'
        native = self.migrated(line, "native")
        self.assertEqual(add_output_lines(native), ['log.add_output = "errors", "rtorrent"'])
        legacy = self.migrated(native, "legacy")
        self.assertEqual(add_output_lines(legacy), [line.strip()])
        self.assertEqual(add_output_lines(self.migrated(legacy, "native")), add_output_lines(native))


if __name__ == "__main__":
    unittest.main()
