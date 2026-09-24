#!/usr/bin/env python3
"""Manually opt in ONLY after packet captures confirm a discovery loop.

This is not a general network repair or an installer. A rule drops UDP to one
computed subnet broadcast, on one tun/tap interface and one destination port,
with source restricted to the explicitly supplied subnet (/16 through /30).
The default --action show only prints a proposal and runs no subprocess.
Use --action apply (alias --apply) or --action remove (alias --remove) only
with --confirm and root. Apply checks every live IPv4 address/prefix; remove
needs no live interface, but uses the same interface/subnet/port and comment.

Only one exact rule is appended/deleted; chains are never flushed. An absent
rule makes remove a no-op. Readback checks rule presence, not packet delivery:
earlier ACCEPT rules can bypass an appended DROP, so verify packet captures.
Do not run concurrently or while changing the interface/firewall configuration.
A timeout/readback error may follow a successful change: inspect before retrying.
Duplicate exact rules are not bulk-deleted; a remaining duplicate is an error.
Executable paths (including --iptables) must be trusted by the operator.
No persistence, service installation or automatic startup is provided.
"""

import argparse
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys


COMMENT = "webui-discovery-loop-guard"


def run_command(argv):
    """Only argument arrays; bounded waits, no shell expansion."""
    return subprocess.run(argv, shell=False, check=False, capture_output=True,
                          text=True, timeout=15)


def validate_live_interface(interface, network):
    """Fail closed if any IPv4 address/prefix differs from the requested subnet."""
    executable = shutil.which("ip")
    if not executable:
        raise RuntimeError("ip executable not found")
    result = run_command([executable, "-j", "-4", "addr", "show", "dev", interface])
    if result.returncode != 0:
        raise RuntimeError("cannot read interface IPv4 configuration")
    try:
        devices = json.loads(result.stdout)
        if not isinstance(devices, list) or len(devices) != 1:
            raise ValueError("expected exactly one interface")
        device = devices[0]
        if device["ifname"] != interface:
            raise ValueError("interface name mismatch")
        addresses = device["addr_info"]
        if not isinstance(addresses, list) or not addresses:
            raise ValueError("no IPv4 addresses")
        for address in addresses:
            if address["family"] != "inet":
                raise ValueError("unexpected address family")
            prefix = address["prefixlen"]
            local = ipaddress.IPv4Address(address["local"])
            if type(prefix) is not int or prefix != network.prefixlen:
                raise ValueError("IPv4 prefix mismatch")
            if local not in network or local in (network.network_address, network.broadcast_address):
                raise ValueError("IPv4 address is not a usable host in the requested subnet")
            if "broadcast" in address and address["broadcast"] != str(network.broadcast_address):
                raise ValueError("reported IPv4 broadcast mismatch")
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"interface verification refused: {exc}") from exc


def rule_exists(command):
    result = run_command(command)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"iptables check failed (exit {result.returncode})")
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--action", choices=("show", "apply", "remove"), default="show")
    actions.add_argument("--apply", dest="action", action="store_const", const="apply")
    actions.add_argument("--remove", dest="action", action="store_const", const="remove")
    parser.add_argument("--confirm", action="store_true", help="explicitly allow the requested mutation")
    parser.add_argument("--interface", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--iptables", default="iptables")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"(?:tun|tap)[A-Za-z0-9_.-]{0,12}", args.interface):
        parser.error("interface must be an exact tun/tap name (at most 15 ASCII characters)")
    if not re.fullmatch(r"[0-9.]+/[0-9]{1,2}", args.subnet):
        parser.error("subnet must be an explicit IPv4 network/numeric-prefix")
    try:
        network = ipaddress.IPv4Network(args.subnet, strict=True)
    except ValueError as exc:
        parser.error(str(exc))
    if not 16 <= network.prefixlen <= 30:
        parser.error("subnet prefix must be between /16 and /30")
    if any(network.overlaps(ipaddress.IPv4Network(block)) for block in
           ("0.0.0.0/8", "127.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4")):
        parser.error("unspecified, loopback, multicast or reserved subnets are forbidden")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    executable = shutil.which(args.iptables)
    if not executable:
        parser.error("iptables executable not found")
    rule = ["-o", args.interface, "-s", str(network), "-p", "udp",
            "-d", f"{network.broadcast_address}/32", "--dport", str(args.port),
            "-m", "comment", "--comment", COMMENT, "-j", "DROP"]
    prefix = [executable, "-w", "5", "-t", "filter"]
    check = [*prefix, "-C", "OUTPUT", *rule]
    add = [*prefix, "-A", "OUTPUT", *rule]
    if args.action == "show":
        print(shlex.join(add))
        return 0
    if not args.confirm:
        parser.error("apply/remove require explicit --confirm")
    if os.geteuid() != 0:
        parser.error("apply/remove require root")
    try:
        applying = args.action == "apply"
        if applying:
            validate_live_interface(args.interface, network)
        present = rule_exists(check)
        if present == applying:
            print("Exact rule already present; unchanged." if present
                  else "Exact rule absent; unchanged.")
            return 0
        command = add if applying else [*prefix, "-D", "OUTPUT", *rule]
        result = run_command(command)
        if result.returncode != 0:
            raise RuntimeError(f"iptables {args.action} failed (exit {result.returncode})")
        if rule_exists(check) != applying:
            raise RuntimeError(f"{args.action} verification failed; inspect rules manually")
        print(f"Exact rule {args.action} completed and verified.")
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
