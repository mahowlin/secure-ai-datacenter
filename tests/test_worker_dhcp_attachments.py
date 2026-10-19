from __future__ import annotations

import copy
import ipaddress
import json
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gitops_renderer import render_gitops
from gitops_renderer.worker_dhcp import _reservation_plan, _server_networks
from site_identity import template_root
from resolver import (
    ResolvedWorkerNetworkAttachment,
    _resolve_hcp_worker_attachment_contract,
    render_template,
    resolve,
    validate,
)
from platform_contract.validation import WORKER_PROFILE_SHAPES


class WorkerDhcpAttachmentTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.platform = resolve(REPO_ROOT)
        self.cluster = next(item for item in self.platform.clusters if item.name == "example-management")
        self.defaults = self.platform.defaults
        dhcp_defaults = self.defaults.gitops_worker_dhcp
        reservations = _reservation_plan(self.cluster, self.defaults, dhcp_defaults)
        self.server_networks = _server_networks(self.cluster, dhcp_defaults, reservations)

    def hcp(self, cluster, name):
        return next(item for item in cluster.hosted_clusters if item.name == name)

    def dynamic_config(self, **updates):
        config = copy.deepcopy(next(
            item for item in self.server_networks
            if item["attachment_kind"] == "multus-l2"
        ))
        config["range_end"] = str(ipaddress.IPv4Address(config["range_start"]) + 1)
        config.update(updates)
        return config

    def direct_config(self, **updates):
        config = self.dynamic_config()
        config.update(updates)
        return config

    def load_server(self, configs, lease_file, *, initialize=True):
        config_json = json.dumps(configs, separators=(",", ":"))
        source = render_template(
            f"{template_root('gitops')}/apps/hcp/management/parent-network/worker-dhcp/server-script.py.j2",
            {
                "interface_config_json_literal": json.dumps(config_json),
                "observed_lease_file_json": json.dumps(str(lease_file)),
                "lease_mount_path_json": json.dumps(str(lease_file.parent)),
                "authority_id_json": json.dumps("test/worker-dhcp"),
                "lease_claim_name_json": json.dumps("test-lease-state"),
                "clock_skew_seconds": 60,
            },
        )
        namespace = {"__name__": "dhcp_fixture_test"}
        exec(compile(source, "server-script.py", "exec"), namespace)
        namespace["__source__"] = source
        namespace["mounted_identity"] = lambda: (1, "test-filesystem-uuid")
        if initialize:
            namespace["initialize_store"]("new-scope", "test/worker-dhcp", "test-lease-state")
            namespace["load_leases"]()
        return namespace

    def packet(self, message_type, mac, *, giaddr="0.0.0.0", requested=None,
               server=None, parameter_request_list=None):
        packet = bytearray(240)
        packet[0:4] = b"\x01\x01\x06\x00"
        packet[4:8] = b"\x12\x34\x56\x78"
        packet[10:12] = b"\x80\x00"
        packet[24:28] = socket.inet_aton(giaddr)
        packet[28:34] = bytes.fromhex(mac.replace(":", ""))
        packet[236:240] = b"\x63\x82\x53\x63"
        packet.extend(bytes([53, 1, message_type]))
        if requested:
            packet.extend(bytes([50, 4]) + socket.inet_aton(requested))
        if server:
            packet.extend(bytes([54, 4]) + socket.inet_aton(server))
        if parameter_request_list:
            packet.extend(bytes([55, len(parameter_request_list), *parameter_request_list]))
        packet.append(255)
        return bytes(packet)

    def response_type(self, module, result):
        return module["parse_options"](result[0])[53][0]

    def option_codes(self, module, result):
        return set(module["parse_options"](result[0]))

    def test_same_domain_dynamic_dora_recovers_durable_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_file = Path(temporary) / "leases.json"
            config = self.dynamic_config()
            module = self.load_server([config], lease_file)
            module["validate_interfaces"]([config])
            discover = self.packet(1, "02:00:00:00:00:01")
            offer = module["process_packet"](discover, config)
            self.assertIsNotNone(offer)
            self.assertEqual(self.response_type(module, offer), 2)
            self.assertEqual(offer[1], ("255.255.255.255", 68))
            offered_address = offer[2]["ip"]

            request = self.packet(
                3, "02:00:00:00:00:01", requested=offered_address,
                server=config["server"],
            )
            ack = module["process_packet"](request, config)
            self.assertEqual(self.response_type(module, ack), 5)
            self.assertEqual(ack[1], ("255.255.255.255", 68))
            self.assertEqual(next(iter(json.loads(lease_file.read_text())["leases"].values()))["state"], "bound")

            module["release_authority_lock"]()
            restarted = self.load_server([config], lease_file, initialize=False)
            restarted["load_leases"]()
            recovered = restarted["process_packet"](request, config)
            self.assertEqual(recovered[2]["ip"], offered_address)

    def test_minimum_options_and_additions_are_server_controlled_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            config["emit_options"] = []
            module = self.load_server([config], Path(temporary) / "leases.json")
            discover = self.packet(
                1,
                "02:00:00:00:00:01",
                parameter_request_list=[1, 3, 6, 12, 26, 58, 59],
            )
            offer = module["process_packet"](discover, config)
            self.assertEqual(self.option_codes(module, offer), {51, 53, 54})

            reservation = {
                "ip": config["range_start"],
                "router": config["router"],
                "dns": config["dns"],
                "hostname": "example-tenant-a-canary",
            }
            additions = {
                "subnet-mask": {1},
                "router": {3},
                "dns": {6},
                "hostname": {12},
                "mtu": {26},
                "renewal": {58, 59},
            }
            for authored_option, expected_codes in additions.items():
                configured = copy.deepcopy(config)
                configured["emit_options"] = [authored_option]
                if authored_option == "mtu":
                    configured["mtu"] = 1450
                payload = module["render_options"](
                    2,
                    reservation,
                    configured,
                    {55: bytes([53, 54, 51])},
                )
                parsed = module["parse_options"](
                    b"\x00" * 236 + b"\x63\x82\x53\x63" + payload
                )
                self.assertEqual(set(parsed), {51, 53, 54} | expected_codes)

            configured = copy.deepcopy(config)
            configured["emit_options"] = ["router"]
            payload = module["render_options"](2, reservation, configured, {55: bytes([53])})
            parsed = module["parse_options"](b"\x00" * 236 + b"\x63\x82\x53\x63" + payload)
            self.assertEqual(set(parsed), {3, 51, 53, 54})

    def test_multus_dora_allocates_dynamically_and_broadcasts_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.direct_config()
            module = self.load_server([config], Path(temporary) / "leases.json")
            self.assertEqual(config["reservations"], [])
            first = module["process_packet"](
                self.packet(1, "02:00:00:00:00:01"), config,
            )
            second = module["process_packet"](
                self.packet(1, "02:00:00:00:00:02"), config,
            )
            self.assertEqual(self.response_type(module, first), 2)
            self.assertEqual(first[1], ("255.255.255.255", 68))
            self.assertNotEqual(first[2]["ip"], second[2]["ip"])
            malformed = bytearray(self.packet(1, "02:00:00:00:00:03"))
            malformed[2] = 5
            self.assertIsNone(module["process_packet"](bytes(malformed), config))

    def test_relay_input_overlap_and_exhaustion_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dynamic = self.dynamic_config()
            module = self.load_server([dynamic], Path(temporary) / "leases.json")
            relayed_from = str(next(ipaddress.IPv4Network(dynamic["address_authority"]).hosts()))
            wrong_server = str(ipaddress.IPv4Address(dynamic["server"]) + 1)
            self.assertIsNone(module["process_packet"](
                self.packet(1, "02:00:00:00:00:01", giaddr=relayed_from), dynamic,
            ))
            self.assertIsNone(module["process_packet"](
                self.packet(3, "02:00:00:00:00:01", server=wrong_server), dynamic,
            ))
            overlapping = self.direct_config(
                scope_id="multus-l2:other:vlan-999",
                name="other",
                network="other",
                subnet="vlan-999",
                interface="eth9",
                address_authority=dynamic["address_authority"],
                server=dynamic["server"],
                range_start=dynamic["range_start"],
                range_end=dynamic["range_end"],
                reservations=[],
            )
            with self.assertRaisesRegex(ValueError, "overlap"):
                module["validate_interfaces"]([dynamic, overlapping])

            for index in range(2):
                packet = self.packet(1, f"02:00:00:00:00:{index + 1:02x}")
                self.assertIsNotNone(module["process_packet"](packet, dynamic))
            exhausted = self.packet(1, "02:00:00:00:00:03")
            self.assertIsNone(module["process_packet"](exhausted, dynamic))

    def test_unenveloped_cross_scope_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_file = Path(temporary) / "leases.json"
            dynamic = self.dynamic_config()
            lease_file.write_text(json.dumps({
                f"{dynamic['scope_id']}:02:00:00:00:00:01": {
                    "scopeId": "other:scope",
                    "network": dynamic["network"],
                    "subnet": dynamic["subnet"],
                    "ip": dynamic["range_start"],
                    "expiresAt": 1,
                },
            }))
            module = self.load_server([dynamic], lease_file, initialize=False)
            with self.assertRaisesRegex(module["LeaseStoreError"], "incompatible lease store version"):
                module["load_leases"]()

    def test_expired_dynamic_lease_is_reclaimed_without_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_file = Path(temporary) / "leases.json"
            dynamic = self.dynamic_config()
            dynamic["range_end"] = dynamic["range_start"]
            module = self.load_server([dynamic], lease_file)
            first_mac = "02:00:00:00:00:01"
            first = module["process_packet"](
                self.packet(1, first_mac), dynamic,
            )
            self.assertEqual(first[2]["ip"], dynamic["range_start"])
            module["LEASES"][module["lease_key"](first_mac, dynamic)]["expiresAt"] = time.time() - 1

            replacement = module["process_packet"](
                self.packet(1, "02:00:00:00:00:02"), dynamic,
            )
            self.assertEqual(replacement[2]["ip"], dynamic["range_start"])
            self.assertNotIn(module["lease_key"](first_mac, dynamic), module["LEASES"])

    def test_request_for_unoffered_address_and_stale_reservation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_file = Path(temporary) / "leases.json"
            dynamic = self.dynamic_config()
            module = self.load_server([dynamic], lease_file)
            mac = "02:00:00:00:00:01"
            offer = module["process_packet"](
                self.packet(1, mac), dynamic,
            )
            self.assertEqual(offer[2]["ip"], dynamic["range_start"])
            unoffered = str(ipaddress.IPv4Address(dynamic["range_start"]) + 1)
            mismatched = self.packet(
                3, mac, requested=unoffered, server=dynamic["server"],
            )
            self.assertIsNone(module["process_packet"](mismatched, dynamic))

            direct = self.direct_config()
            stale_ip = str(ipaddress.IPv4Address(direct["range_end"]) + 1)
            stale = {
                "ip": stale_ip,
                "mac": mac,
                "network": direct["network"],
                "subnet": direct["subnet"],
                "scopeId": direct["scope_id"],
                "mode": direct["mode"],
                "state": "bound",
                "expiresAt": time.time() + 300,
            }
            key = f"{direct['scope_id']}:{mac}"
            self.assertFalse(module["valid_lease"](
                key, stale, {direct["scope_id"]: direct},
            ))

    def test_renderer_uses_one_endpoint_per_scope(self) -> None:
        dhcp_defaults = self.defaults.gitops_worker_dhcp
        reservations = _reservation_plan(self.cluster, self.defaults, dhcp_defaults)
        networks = _server_networks(self.cluster, dhcp_defaults, reservations)
        self.assertEqual({item["transport"] for item in networks}, {"broadcast"})
        self.assertEqual(len(networks), 1)
        self.assertEqual(networks[0]["interface"], "eth0")
        self.assertEqual(networks[0]["attachment_kind"], "multus-l2")
        self.assertEqual(networks[0]["reservations"], [])
        self.assertNotIn("relay_server", networks[0])

    def test_worker_dhcp_fixture_mounts_exact_retained_claim(self) -> None:
        rendered = render_gitops(
            self.cluster,
            self.platform.fabric.fabric_asn,
            self.defaults,
        )
        base = (
            "apps/hcp/management/parent-network/overlays/example-management/"
            "worker-dhcp"
        )
        self.assertFalse(any(path.startswith(f"{base}/example-tenant-a/") for path in rendered))
        for component in ("example-shared",):
            prefix = f"{base}/{component}"
            self.assertIn(f"{prefix}/persistentvolumeclaim.yaml", rendered)

            kustomization = yaml.safe_load(rendered[f"{prefix}/kustomization.yaml"])
            self.assertIn(
                "persistentvolumeclaim.yaml",
                kustomization["resources"],
            )

            pvc = yaml.safe_load(rendered[f"{prefix}/persistentvolumeclaim.yaml"])
            self.assertEqual(pvc["spec"]["storageClassName"], "px-csi-replicated-default")
            self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "2Gi")
            self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
            self.assertEqual(pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"], "Prune=false")

            vm = yaml.safe_load(rendered[f"{prefix}/virtualmachine.yaml"])
            self.assertEqual(vm["spec"]["runStrategy"], "Manual")
            pod_spec = vm["spec"]["template"]["spec"]
            network_data = yaml.safe_load(next(
                volume["cloudInitNoCloud"]["networkData"] for volume in pod_spec["volumes"]
                if volume["name"] == "cloudinit"
            ))
            self.assertEqual(network_data["ethernets"]["eth0"]["addresses"], ["198.18.14.5/28"])
            devices = pod_spec["domain"]["devices"]
            self.assertNotIn("filesystems", devices)
            self.assertEqual(next(d for d in devices["disks"] if d["name"] == "lease-state")["disk"]["bus"], "virtio")
            self.assertEqual(next(v for v in pod_spec["volumes"] if v["name"] == "lease-state")["persistentVolumeClaim"]["claimName"], pvc["metadata"]["name"])
            self.assertNotIn("evictionStrategy", pod_spec)

            cloud_init = yaml.safe_load(rendered[f"{prefix}/cloud-init-config.yaml"])
            user_data = cloud_init["stringData"]["userdata"]
            self.assertNotIn("virtiofs", user_data)
            self.assertNotIn("RequiresMountsFor", user_data)
            self.assertIn(self.defaults.gitops_worker_dhcp["observedLeaseFile"], user_data)
            self.assertIn("ExecStartPre=/usr/local/sbin/saif-worker-dhcp-lease-disk.sh --verify", user_data)

    def test_missing_corrupt_incompatible_and_duplicate_state_refuse_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_file = Path(temporary) / "leases.json"
            config = self.dynamic_config()
            module = self.load_server([config], lease_file, initialize=False)
            with self.assertRaisesRegex(module["LeaseStoreError"], "missing"):
                module["load_leases"]()
            lease_file.write_text("{broken")
            with self.assertRaisesRegex(module["LeaseStoreError"], "decoded"):
                module["load_leases"]()

            lease_file.unlink()
            module["initialize_store"]("new-scope", "test/worker-dhcp", "test-lease-state")
            baseline = json.loads(lease_file.read_text())
            changed = copy.deepcopy(baseline)
            changed["scopeDigest"] = "other-scope"
            lease_file.write_text(json.dumps(changed))
            with self.assertRaisesRegex(module["LeaseStoreError"], "scopeDigest"):
                module["load_leases"]()

            now = time.time()
            changed = copy.deepcopy(baseline)
            for suffix in (1, 2):
                mac = f"02:00:00:00:00:{suffix:02x}"
                changed["leases"][f"{config['scope_id']}:{mac}"] = {
                    "ip": config["range_start"], "mac": mac,
                    "network": config["network"], "subnet": config["subnet"],
                    "scopeId": config["scope_id"], "mode": "dynamic",
                    "state": "bound", "issuedAt": now,
                    "expiresAt": now + 300,
                }
            changed["maxIssuedLeaseSeconds"] = 300
            lease_file.write_text(json.dumps(changed))
            with self.assertRaisesRegex(module["LeaseStoreError"], "duplicate live address"):
                module["load_leases"]()

    def test_write_faults_stop_all_packet_processing_before_offer_or_ack(self) -> None:
        for operation in ("replace", "fsync"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                config = self.dynamic_config()
                lease_file = Path(temporary) / "leases.json"
                module = self.load_server([config], lease_file)
                before = lease_file.read_bytes()
                with mock.patch.object(module["os"], operation, side_effect=OSError("injected store fault")):
                    with self.assertRaisesRegex(OSError, "injected store fault"):
                        module["process_packet"](self.packet(1, "02:00:00:00:00:01"), config)
                self.assertTrue(module["FAILED"].is_set())
                with self.assertRaises(module["LeaseStoreError"]):
                    module["process_packet"](self.packet(1, "02:00:00:00:00:02"), config)
                if operation == "replace":
                    self.assertEqual(lease_file.read_bytes(), before)

    def test_directory_fsync_after_rename_stops_before_offer_or_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            module = self.load_server([config], lease_file)
            original_fsync = module["os"].fsync
            calls = 0

            def fail_directory_fsync(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected directory fsync fault")
                return original_fsync(descriptor)

            with mock.patch.object(module["os"], "fsync", side_effect=fail_directory_fsync):
                with self.assertRaisesRegex(OSError, "directory fsync fault"):
                    module["process_packet"](self.packet(1, "02:00:00:00:00:01"), config)
            self.assertEqual(calls, 2)
            self.assertTrue(module["FAILED"].is_set())
            with self.assertRaises(module["LeaseStoreError"]):
                module["process_packet"](self.packet(3, "02:00:00:00:00:01", requested=config["range_start"]), config)

    def test_storage_fault_stops_every_serving_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            second = copy.deepcopy(config)
            second["interface"] = "eth9"
            module = self.load_server([config], Path(temporary) / "leases.json")
            ready = threading.Barrier(2)
            sockets = []
            packet = self.packet(1, "02:00:00:00:00:01")

            class FakeSocket:
                def __init__(self):
                    self.interface = None
                    self.received = False
                    self.sent = []
                    self.closed = False
                    sockets.append(self)

                def setsockopt(self, level, option, value):
                    if option == module["SO_BINDTODEVICE"]:
                        self.interface = value.rstrip(b"\0").decode()

                def bind(self, address):
                    ready.wait(timeout=2)

                def settimeout(self, seconds):
                    pass

                def recvfrom(self, size):
                    if self.interface == config["interface"] and not self.received:
                        self.received = True
                        return packet, ("0.0.0.0", 68)
                    time.sleep(0.005)
                    raise socket.timeout()

                def sendto(self, payload, destination):
                    self.sent.append((payload, destination))

                def close(self):
                    self.closed = True

            with mock.patch.object(module["socket"], "socket", side_effect=lambda *args: FakeSocket()), \
                 mock.patch.object(module["os"], "fsync", side_effect=OSError("injected storage fault")):
                threads = [threading.Thread(target=module["serve"], args=(item,)) for item in (config, second)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=3)
            self.assertTrue(module["FAILED"].is_set())
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(sockets), 2)
            self.assertTrue(all(sock.closed and not sock.sent for sock in sockets))

    def test_independent_process_cannot_load_or_initialize_while_writer_holds_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            module = self.load_server([config], lease_file)
            script = Path(temporary) / "rendered-server.py"
            script.write_text(module["__source__"])
            lock_file = Path(temporary) / ".authority.lock"
            lock_inode = lock_file.stat().st_ino
            child = """
import sys
from pathlib import Path
namespace = {'__name__': 'dhcp_child'}
source = Path(sys.argv[1]).read_text()
exec(compile(source, sys.argv[1], 'exec'), namespace)
namespace['mounted_identity'] = lambda: (1, 'test-filesystem-uuid')
try:
    if sys.argv[2] == 'load':
        namespace['load_leases']()
    else:
        namespace['initialize_store']('new-scope', 'test/worker-dhcp', 'test-lease-state')
except namespace['LeaseStoreError'] as exc:
    print(exc)
    sys.exit(17)
print('READY')
namespace['release_authority_lock']()
"""
            for action in ("load", "initialize"):
                result = subprocess.run(
                    [sys.executable, "-c", child, str(script), action],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertIn("another DHCP authority owns", result.stdout)

            offer = module["process_packet"](self.packet(1, "02:00:00:00:00:01"), config)
            self.assertIsNotNone(offer)
            self.assertEqual(lock_file.stat().st_ino, lock_inode)
            module["release_authority_lock"]()
            result = subprocess.run(
                [sys.executable, "-c", child, str(script), "load"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("READY", result.stdout)

    def test_mount_loss_and_reinitialization_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            module = self.load_server([config], lease_file)
            with self.assertRaisesRegex(module["LeaseStoreError"], "serving authority cannot initialize"):
                module["initialize_store"]("new-scope", "test/worker-dhcp", "test-lease-state")
            module["mounted_identity"] = lambda: (2, "other-filesystem-uuid")
            with self.assertRaisesRegex(module["LeaseStoreError"], "retained mount changed"):
                module["process_packet"](self.packet(1, "02:00:00:00:00:01"), config)
            self.assertTrue(module["FAILED"].is_set())

    def test_external_state_loss_and_concurrent_offers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            module = self.load_server([config], lease_file)
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def offer(suffix):
                try:
                    barrier.wait()
                    results.append(module["process_packet"](
                        self.packet(1, f"02:00:00:00:00:{suffix:02x}"), config,
                    ))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=offer, args=(index,)) for index in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len({result[2]["ip"] for result in results}), 2)
            self.assertEqual(len(json.loads(lease_file.read_text())["leases"]), 2)

            lease_file.unlink()
            with self.assertRaisesRegex(module["LeaseStoreError"], "disappeared"):
                module["process_packet"](self.packet(1, "02:00:00:00:00:03"), config)
            self.assertTrue(module["FAILED"].is_set())

    def test_quarantine_uses_measured_prior_lease_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            module = self.load_server([config], lease_file, initialize=False)
            with self.assertRaisesRegex(module["LeaseStoreError"], "maximum actually issued"):
                module["initialize_store"](
                    "quarantine", "test/worker-dhcp", "test-lease-state",
                    old_fenced_at=time.time(), margin_seconds=60,
                )
            fenced_at = time.time()
            module["initialize_store"](
                "quarantine", "test/worker-dhcp", "test-lease-state",
                max_prior_seconds=300, old_fenced_at=fenced_at, margin_seconds=60,
            )
            retained = json.loads(lease_file.read_text())
            self.assertAlmostEqual(retained["quarantineUntil"], fenced_at + 360)
            with self.assertRaisesRegex(module["LeaseStoreError"], "recovery quarantine"):
                module["load_leases"]()

    def test_import_preserves_active_legacy_address_before_first_offer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.dynamic_config()
            lease_file = Path(temporary) / "leases.json"
            legacy_file = Path(temporary) / "old-leases.json"
            now = time.time()
            mac = "02:00:00:00:00:01"
            legacy_file.write_text(json.dumps({
                f"{config['scope_id']}:{mac}": {
                    "ip": config["range_start"], "mac": mac,
                    "network": config["network"], "subnet": config["subnet"],
                    "scopeId": config["scope_id"], "mode": "dynamic",
                    "state": "bound", "expiresAt": now + 250,
                },
            }))
            module = self.load_server([config], lease_file, initialize=False)
            module["initialize_store"](
                "import", "test/worker-dhcp", "test-lease-state",
                legacy_file=str(legacy_file), max_prior_seconds=300,
                old_fenced_at=now - 10, margin_seconds=60,
            )
            module["load_leases"]()
            offer = module["process_packet"](
                self.packet(1, "02:00:00:00:00:02"), config,
            )
            self.assertNotEqual(offer[2]["ip"], config["range_start"])
            self.assertEqual(len(json.loads(lease_file.read_text())["leases"]), 2)

    def test_multus_dhcp_does_not_render_a_workload_mutator(self) -> None:
        rendered = render_gitops(
            self.cluster, self.platform.fabric.fabric_asn, self.defaults,
        )
        dhcp_root = "apps/hcp/management/parent-network/overlays/example-management/worker-dhcp"
        self.assertIn(f"{dhcp_root}/example-shared/virtualmachine.yaml", rendered)
        self.assertFalse(any("workload-mutator" in path for path in rendered))


if __name__ == "__main__":
    unittest.main()
