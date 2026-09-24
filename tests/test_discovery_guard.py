"""Offline stdlib tests; never execute ip or iptables, or require root."""

import contextlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "discovery_guard.py"
BASE = ["--interface", "tun-test", "--subnet", "198.51.100.0/30", "--port", "45678"]


class DiscoveryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SCRIPT.is_file(), "discovery_guard.py has not been implemented"
        spec = importlib.util.spec_from_file_location("discovery_guard", SCRIPT)
        cls.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.guard)

    def setUp(self):
        self.run = self.enterContext(patch.object(self.guard.subprocess, "run"))
        self.which = self.enterContext(patch.object(self.guard.shutil, "which", side_effect=lambda name: name))
        self.uid = self.enterContext(patch.object(self.guard.os, "geteuid", return_value=1000))
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.err = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def invoke(self, extra=(), base=None):
        try:
            return self.guard.main([*(BASE if base is None else base), *extra])
        except SystemExit as exc:
            return exc.code

    def replace_arg(self, flag, value):
        args = BASE.copy()
        args[args.index(flag) + 1] = value
        return args

    def test_unsafe_interfaces_are_rejected(self):
        for name in ("lo", "eth0", "wlan0", "tun-test+", "tun-test;id", "tun$(id)", "tun test", "tun\ntest", "tun" + "x" * 13):
            with self.subTest(name=name):
                self.assertNotEqual(self.invoke(base=self.replace_arg("--interface", name)), 0)
        self.run.assert_not_called()

    def test_unsafe_subnets_are_rejected(self):
        for subnet in ("0.0.0.0/0", "198.50.0.0/15", "198.51.100.0/31", "198.51.100.0/32", "127.0.0.0/16", "224.0.0.0/16", "0.0.0.0/16", "240.0.0.0/16", "198.51.100.1/30", "198.51.100.0", "198.51.100.0/255.255.255.252", "::/16", "198.51.100.0/30;id"):
            with self.subTest(subnet=subnet):
                self.assertNotEqual(self.invoke(base=self.replace_arg("--subnet", subnet)), 0)
        self.run.assert_not_called()

    def test_safe_prefix_boundaries_and_tap_interface(self):
        for subnet in ("198.51.0.0/16", "198.51.100.0/30"):
            with self.subTest(subnet=subnet):
                args = self.replace_arg("--subnet", subnet)
                args[args.index("--interface") + 1] = "tap-test"
                self.assertEqual(self.invoke(base=args), 0)
        self.run.assert_not_called()

    def test_port_is_explicit_single_numeric_and_in_range(self):
        for port in ("0", "65536", "-1", "53:54", "domain", "45678;id"):
            with self.subTest(port=port):
                self.assertNotEqual(self.invoke(base=self.replace_arg("--port", port)), 0)
        self.assertNotEqual(self.invoke(base=BASE[:-2]), 0)
        self.run.assert_not_called()

    def test_missing_executable_is_rejected_without_execution(self):
        self.which.return_value = None
        self.which.side_effect = None
        self.assertNotEqual(self.invoke(), 0)
        self.run.assert_not_called()

    @staticmethod
    def result(code=0, text=""):
        return subprocess.CompletedProcess([], code, stdout=text, stderr="")

    @staticmethod
    def addresses(local="198.51.100.1", prefix=30, name="tun-test"):
        return [{"ifname": name, "addr_info": [{"family": "inet", "local": local, "prefixlen": prefix}]}]

    def ip_result(self, data=None):
        return self.result(text=json.dumps(self.addresses() if data is None else data))

    def test_mutations_require_confirm_even_for_root(self):
        self.uid.return_value = 0
        for action in ("apply", "remove"):
            with self.subTest(action=action):
                self.assertNotEqual(self.invoke(["--action", action]), 0)
                self.assertIn("--confirm", self.err.getvalue())
        self.run.assert_not_called()

    def test_mutations_require_root(self):
        for action in ("apply", "remove"):
            with self.subTest(action=action):
                self.assertNotEqual(self.invoke(["--action", action, "--confirm"]), 0)
                self.assertIn("root", self.err.getvalue())
        self.run.assert_not_called()

    def test_explicit_show_with_confirm_remains_offline(self):
        self.assertEqual(self.invoke(["--action", "show", "--confirm"]), 0)
        self.run.assert_not_called()
        self.uid.assert_not_called()

    def test_apply_checks_interface_then_adds_and_verifies_one_rule(self):
        self.uid.return_value = 0
        self.run.side_effect = [self.ip_result(), self.result(1), self.result(), self.result()]
        self.assertEqual(self.invoke(["--apply", "--confirm"]), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0], ["ip", "-j", "-4", "addr", "show", "dev", "tun-test"])
        expected = ["-o", "tun-test", "-s", "198.51.100.0/30", "-p", "udp", "-d", "198.51.100.3/32", "--dport", "45678", "-m", "comment", "--comment", "webui-discovery-loop-guard", "-j", "DROP"]
        for command, operation in zip(commands[1:], ("-C", "-A", "-C")):
            self.assertEqual(command, ["iptables", "-w", "5", "-t", "filter", operation, "OUTPUT", *expected])
        self.assertEqual(len(commands), 4)
        for call in self.run.call_args_list:
            self.assertIsInstance(call.args[0], list)
            self.assertIs(call.kwargs.get("shell"), False)
            self.assertGreater(call.kwargs["timeout"], 0)

    def test_apply_is_idempotent(self):
        self.uid.return_value = 0
        self.run.side_effect = [self.ip_result(), self.result()]
        self.assertEqual(self.invoke(["--action", "apply", "--confirm"]), 0)
        self.assertEqual(self.run.call_count, 2)
        self.assertIn("-C", self.run.call_args.args[0])

    def test_apply_rejects_every_interface_address_mismatch(self):
        self.uid.return_value = 0
        extra = self.addresses()
        extra[0]["addr_info"].append({"family": "inet", "local": "198.51.100.5", "prefixlen": 30})
        cases = [[], {}, [{}], self.addresses(prefix=29), self.addresses(local="198.51.100.5"), self.addresses(local="198.51.100.0"), self.addresses(local="198.51.100.3"), self.addresses(name="tap-test"), self.addresses(prefix="30"), self.addresses(local="not-an-address"), [{"ifname": "tun-test", "addr_info": []}], extra, self.addresses() * 2]
        for data in cases:
            with self.subTest(data=data):
                self.run.reset_mock()
                self.run.return_value = self.ip_result(data)
                self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)
                self.assertEqual(self.run.call_count, 1)
                self.assertEqual(self.run.call_args.args[0][0], "ip")

    def test_apply_rejects_reported_broadcast_mismatch(self):
        self.uid.return_value = 0
        data = self.addresses()
        data[0]["addr_info"][0]["broadcast"] = "198.51.100.2"
        self.run.return_value = self.ip_result(data)
        self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)
        self.assertEqual(self.run.call_count, 1)

    def test_apply_accepts_matching_reported_broadcast(self):
        self.uid.return_value = 0
        data = self.addresses()
        data[0]["addr_info"][0]["broadcast"] = "198.51.100.3"
        self.run.side_effect = [self.ip_result(data), self.result()]
        self.assertEqual(self.invoke(["--apply", "--confirm"]), 0)
        self.assertEqual(self.run.call_count, 2)

    def test_apply_refuses_ip_failure_or_invalid_json(self):
        self.uid.return_value = 0
        for result in (self.result(1), self.result(text="not-json")):
            with self.subTest(result=result):
                self.run.reset_mock()
                self.run.return_value = result
                self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)
                self.assertEqual(self.run.call_count, 1)

    def test_apply_refuses_missing_ip_tool(self):
        self.uid.return_value = 0
        self.which.side_effect = lambda name: None if name == "ip" else name
        self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)
        self.run.assert_not_called()

    def test_check_errors_never_trigger_add(self):
        self.uid.return_value = 0
        self.run.side_effect = [self.ip_result(), self.result(2)]
        self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)
        self.assertEqual(self.run.call_count, 2)

    def test_failed_add_or_failed_readback_is_not_success(self):
        self.uid.return_value = 0
        for outcomes in ((self.result(2),), (self.result(), self.result(1))):
            with self.subTest(outcomes=outcomes):
                self.run.reset_mock()
                self.run.side_effect = [self.ip_result(), self.result(1), *outcomes]
                self.assertNotEqual(self.invoke(["--apply", "--confirm"]), 0)

    def test_remove_checks_exact_rule_without_reading_interface(self):
        self.uid.return_value = 0
        self.which.side_effect = lambda name: None if name == "ip" else name
        self.run.side_effect = [self.result(), self.result(), self.result(1)]
        self.assertEqual(self.invoke(["--remove", "--confirm"]), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(len(commands), 3)
        for command, operation in zip(commands, ("-C", "-D", "-C")):
            self.assertEqual(command[:7], ["iptables", "-w", "5", "-t", "filter", operation, "OUTPUT"])
            self.assertEqual(command[7:], commands[0][7:])
            self.assertIn("webui-discovery-loop-guard", command)
            self.assertIn("198.51.100.0/30", command)
            self.assertIn("198.51.100.3/32", command)
            self.assertIn("45678", command)
        self.assertNotIn("ip", [call.args[0] for call in self.which.call_args_list])

    def test_remove_absent_rule_does_not_delete_anything(self):
        self.uid.return_value = 0
        self.run.return_value = self.result(1)
        self.assertEqual(self.invoke(["--action", "remove", "--confirm"]), 0)
        self.assertEqual(self.run.call_count, 1)
        self.assertIn("-C", self.run.call_args.args[0])

    def test_remove_changed_parameters_never_deletes_other_rules(self):
        self.uid.return_value = 0
        stored_rule = ["-o", "tun-test", "-s", "198.51.100.0/30", "-p", "udp", "-d", "198.51.100.3/32", "--dport", "45678", "-m", "comment", "--comment", "webui-discovery-loop-guard", "-j", "DROP"]
        def fake_run(argv, **kwargs):
            self.assertIn("-C", argv)
            return self.result(0 if argv[7:] == stored_rule else 1)
        self.run.side_effect = fake_run
        for flag, value in (("--port", "45679"), ("--interface", "tap-test"), ("--subnet", "198.51.100.4/30")):
            with self.subTest(flag=flag):
                self.assertEqual(self.invoke(["--remove", "--confirm"], self.replace_arg(flag, value)), 0)
        self.assertEqual(self.run.call_count, 3)

    def test_remove_errors_and_remaining_duplicates_are_not_success(self):
        self.uid.return_value = 0
        for outcomes in ((self.result(2),), (self.result(), self.result(2)), (self.result(), self.result(), self.result())):
            with self.subTest(outcomes=outcomes):
                self.run.reset_mock()
                self.run.side_effect = outcomes
                self.assertNotEqual(self.invoke(["--remove", "--confirm"]), 0)
                self.assertLessEqual(self.run.call_count, 3)

    def test_custom_executable_shell_characters_are_literal(self):
        executable = str(Path("mock-bin") / "iptables;literal name")
        self.uid.return_value = 0
        self.run.return_value = self.result(1)
        self.assertEqual(self.invoke(["--remove", "--confirm", "--iptables", executable]), 0)
        self.which.assert_any_call(executable)
        self.assertEqual(self.run.call_args.args[0][0], executable)
        self.assertIs(self.run.call_args.kwargs["shell"], False)
        self.run.reset_mock()
        self.assertEqual(self.invoke(["--iptables", executable]), 0)
        self.run.assert_not_called()
        self.assertIn("'" + executable + "'", self.out.getvalue())

    def test_subprocess_timeout_or_os_error_is_reported(self):
        self.uid.return_value = 0
        for error in (OSError("mock executable failure"), subprocess.TimeoutExpired("mock", 15)):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertNotEqual(self.invoke(["--remove", "--confirm"]), 0)
                self.assertIn("Error:", self.err.getvalue())

    def test_action_conflicts_and_abbreviated_confirm_are_rejected(self):
        self.uid.return_value = 0
        for flags in (("--apply", "--remove", "--confirm"), ("--apply", "--conf")):
            with self.subTest(flags=flags):
                self.assertNotEqual(self.invoke(flags), 0)
        self.run.assert_not_called()

    def test_default_show_is_offline_and_narrow(self):
        self.assertEqual(self.invoke(), 0)
        self.run.assert_not_called()
        self.uid.assert_not_called()
        text = self.out.getvalue()
        for token in ("OUTPUT", "-o tun-test", "-p udp", "198.51.100.3/32", "--dport 45678", "webui-discovery-loop-guard", "-j DROP"):
            self.assertIn(token, text)
        self.assertNotIn("-F", text)


if __name__ == "__main__":
    unittest.main()
