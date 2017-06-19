#!/usr/bin/env python

"""Mininet tests for FAUCET."""

# pylint: disable=missing-docstring

import os
import re
import shutil
import threading
import time
import unittest

from SimpleHTTPServer import SimpleHTTPRequestHandler
from BaseHTTPServer import HTTPServer

import ipaddress
import yaml

from mininet.net import Mininet

import faucet_mininet_test_base
import faucet_mininet_test_util
import faucet_mininet_test_topo


class FaucetTest(faucet_mininet_test_base.FaucetTestBase):

    pass


@unittest.skip('currently flaky')
class FaucetAPITest(faucet_mininet_test_base.FaucetTestBase):
    """Test the Faucet API."""

    def setUp(self):
        self.tmpdir = self._tmpdir_name()
        name = 'faucet'
        self._set_var_path(name, 'FAUCET_CONFIG', 'config/testconfigv2-simple.yaml')
        self._set_var_path(name, 'FAUCET_LOG', 'faucet.log')
        self._set_var_path(name, 'FAUCET_EXCEPTION_LOG', 'faucet-exception.log')
        self._set_var_path(name, 'API_TEST_RESULT', 'result.txt')
        self.results_file = self.env[name]['API_TEST_RESULT']
        shutil.copytree('config', os.path.join(self.tmpdir, 'config'))
        self.dpid = str(0xcafef00d)
        self._set_prom_port(name)
        self.of_port, _ = faucet_mininet_test_util.find_free_port(
            self.ports_sock, self._test_name())
        self.topo = faucet_mininet_test_topo.FaucetSwitchTopo(
            self.ports_sock,
            dpid=self.dpid,
            n_untagged=7,
            test_name=self._test_name())
        self.net = Mininet(
            self.topo,
            controller=faucet_mininet_test_topo.FaucetAPI(
                name=name,
                tmpdir=self.tmpdir,
                env=self.env[name],
                port=self.of_port))
        self.net.start()
        self.wait_for_tcp_listen(self._get_controller(), self.of_port)

    def test_api(self):
        for _ in range(10):
            try:
                with open(self.results_file, 'r') as results:
                    result = results.read().strip()
                    self.assertEquals('pass', result, result)
                    return
            except IOError:
                time.sleep(1)
        self.fail('no result from API test')


class FaucetUntaggedTest(FaucetTest):
    """Basic untagged VLAN test."""

    N_UNTAGGED = 4
    N_TAGGED = 0
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def setUp(self):
        super(FaucetUntaggedTest, self).setUp()
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid,
            n_tagged=self.N_TAGGED, n_untagged=self.N_UNTAGGED)
        self.start_net()

    def test_untagged(self):
        """All hosts on the same untagged VLAN should have connectivity."""
        self.ping_all_when_learned()
        self.flap_all_switch_ports()
        self.gauge_smoke_test()
        self.prometheus_smoke_test()


class FaucetUntaggedTcpIPv4IperfTest(FaucetUntaggedTest):

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        second_host_ip = ipaddress.ip_address(unicode(second_host.IP()))
        for _ in range(3):
            self.ping_all_when_learned()
            self.one_ipv4_ping(first_host, second_host_ip)
            self.verify_iperf_min(
                ((first_host, self.port_map['port_1']),
                 (second_host, self.port_map['port_2'])),
                1, second_host_ip)
            self.flap_all_switch_ports()


class FaucetUntaggedTcpIPv6IperfTest(FaucetUntaggedTest):

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        first_host_ip = ipaddress.ip_interface(u'fc00::1:1/112')
        second_host_ip = ipaddress.ip_interface(u'fc00::1:2/112')
        self.add_host_ipv6_address(first_host, first_host_ip)
        self.add_host_ipv6_address(second_host, second_host_ip)
        for _ in range(3):
            self.ping_all_when_learned()
            self.one_ipv6_ping(first_host, second_host_ip.ip)
            self.verify_iperf_min(
                ((first_host, self.port_map['port_1']),
                 (second_host, self.port_map['port_2'])),
                1, second_host_ip.ip)
            self.flap_all_switch_ports()


class FaucetSanityTest(FaucetUntaggedTest):
    """Sanity test - make sure test environment is correct before running all tess."""

    pass


class FaucetUntaggedInfluxTest(FaucetUntaggedTest):
    """Basic untagged VLAN test with Influx."""

    def get_gauge_watcher_config(self):
        return """
    port_stats:
        dps: ['faucet-1']
        type: 'port_stats'
        interval: 2
        db: 'influx'
    port_state:
        dps: ['faucet-1']
        type: 'port_state'
        interval: 2
        db: 'influx'
"""

    def test_untagged_influx_down(self):
        self.ping_all_when_learned()
        self.verify_no_exception(self.env['faucet']['FAUCET_EXCEPTION_LOG'])

    def test_untagged(self):

        influx_log = os.path.join(self.tmpdir, 'influx.log')

        class PostHandler(SimpleHTTPRequestHandler):

            def do_POST(self):
                content_len = int(self.headers.getheader('content-length', 0))
                content = self.rfile.read(content_len)
                open(influx_log, 'a').write(content)
                return self.send_response(204)

        server = HTTPServer(('', self.influx_port), PostHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        self.ping_all_when_learned()
        for _ in range(3):
            if os.path.exists(influx_log):
                break
            time.sleep(2)
        server.shutdown()
        self.assertTrue(os.path.exists(influx_log))


class FaucetNailedForwardingTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
acls:
    1:
        - rule:
            dl_dst: "0e:00:00:00:02:02"
            actions:
                output:
                    port: b2
        - rule:
            dl_type: 0x806
            dl_dst: "ff:ff:ff:ff:ff:ff"
            arp_tpa: "10.0.0.2"
            actions:
                output:
                    port: b2
        - rule:
            actions:
                allow: 0
    2:
        - rule:
            dl_dst: "0e:00:00:00:01:01"
            actions:
                output:
                    port: b1
        - rule:
            dl_type: 0x806
            dl_dst: "ff:ff:ff:ff:ff:ff"
            arp_tpa: "10.0.0.1"
            actions:
                output:
                    port: b1
        - rule:
            actions:
                allow: 0
    3:
        - rule:
            actions:
                allow: 0
    4:
        - rule:
            actions:
                allow: 0
"""

    CONFIG = """
        interfaces:
            b1:
                number: %(port_1)d
                native_vlan: 100
                acl_in: 1
            b2:
                number: %(port_2)d
                native_vlan: 100
                acl_in: 2
            b3:
                number: %(port_3)d
                native_vlan: 100
                acl_in: 3
            b4:
                number: %(port_4)d
                native_vlan: 100
                acl_in: 4
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        first_host.setMAC("0e:00:00:00:01:01")
        second_host.setMAC("0e:00:00:00:02:02")
        self.one_ipv4_ping(
            first_host, second_host.IP(), require_host_learned=False)
        self.one_ipv4_ping(
            second_host, first_host.IP(), require_host_learned=False)



class FaucetUntaggedLLDPBlockedTest(FaucetUntaggedTest):

    def test_untagged(self):
        self.ping_all_when_learned()
        self.assertTrue(self.verify_lldp_blocked())


class FaucetUntaggedCDPTest(FaucetUntaggedTest):

    def test_untagged(self):
        self.ping_all_when_learned()
        self.assertFalse(self.is_cdp_blocked())


class FaucetUntaggedLLDPUnblockedTest(FaucetUntaggedTest):

    CONFIG = """
        drop_lldp: False
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.ping_all_when_learned()
        self.assertFalse(self.verify_lldp_blocked())


class FaucetZodiacUntaggedTest(FaucetUntaggedTest):
    """Zodiac has only 3 ports available, and one controller so no Gauge."""

    RUN_GAUGE = False
    N_UNTAGGED = 3

    def test_untagged(self):
        """All hosts on the same untagged VLAN should have connectivity."""
        self.ping_all_when_learned()
        self.flap_all_switch_ports()
        self.ping_all_when_learned()


class FaucetTaggedAndUntaggedVlanTest(FaucetTest):
    """Test mixture of tagged and untagged hosts on the same VLAN."""

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "mixed"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def setUp(self):
        super(FaucetTaggedAndUntaggedVlanTest, self).setUp()
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid, n_tagged=1, n_untagged=3)
        self.start_net()

    def test_untagged(self):
        """Test connectivity including after port flapping."""
        self.ping_all_when_learned()
        self.flap_all_switch_ports()
        self.ping_all_when_learned()


class FaucetZodiacTaggedAndUntaggedVlanTest(FaucetUntaggedTest):

    RUN_GAUGE = False
    N_TAGGED = 1
    N_UNTAGGED = 2
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "mixed"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        """Test connectivity including after port flapping."""
        self.ping_all_when_learned()
        self.flap_all_switch_ports()
        self.ping_all_when_learned()


class FaucetUntaggedMaxHostsTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        max_hosts: 2
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""
    def test_untagged(self):
        self.net.pingAll()
        learned_hosts = [
            host for host in self.net.hosts if self.host_learned(host)]
        self.assertEquals(2, len(learned_hosts))
        self.assertEquals(2, self.scrape_prometheus_var(
            'vlan_hosts_learned', {'vlan': '100'}))


class FaucetMaxHostsPortTest(FaucetUntaggedTest):

    MAX_HOSTS = 3
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
                max_hosts: 3
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        self.ping_all_when_learned()
        for i in range(10, 10+(self.MAX_HOSTS*2)):
            mac_intf = 'mac%u' % i
            mac_ipv4 = '10.0.0.%u' % i
            second_host.cmd('ip link add link %s %s type macvlan' % (
                second_host.defaultIntf(), mac_intf))
            second_host.cmd('ip address add %s/24 dev %s' % (
                mac_ipv4, mac_intf))
            second_host.cmd('ip link set dev %s up' % mac_intf)
            second_host.cmd('ping -c1 -I%s %s &' % (mac_intf, first_host.IP()))
        flows = self.get_matching_flows_on_dpid(
            self.dpid,
            {u'dl_vlan': u'100', u'in_port': int(self.port_map['port_2'])},
            table_id=3)
        self.assertEquals(self.MAX_HOSTS, len(flows))
        self.assertEquals(
            self.MAX_HOSTS,
            len(self.scrape_prometheus_var(
                'learned_macs',
                {'port': self.port_map['port_2'], 'vlan': '100'},
                multiple=True)))


class FaucetHostsTimeoutPrometheusTest(FaucetUntaggedTest):
    """Test for hosts that have been learnt are exported via prometheus.
       Hosts should timeout, and the exported prometheus values should
       be overwritten.
       If the maximum number of MACs at any one time is 5, then only 5 values
       should be exported, even if over 2 hours, there are 100 MACs learnt
    """
    TIMEOUT = 10
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
"""

    CONFIG = """
        timeout: 10
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def mac_as_int(self, mac):
        return long(mac.replace(':', ''), 16)

    def macs_learned_on_port(self, port):
        port_learned_macs_prom = self.scrape_prometheus_var(
            'learned_macs', {'port': str(port), 'vlan': '100'},
            default=[], multiple=True)
        macs_learned = []
        for _, mac_int in port_learned_macs_prom:
            if mac_int:
                macs_learned.append(mac_int)
        return macs_learned

    def verify_hosts_learned(self, hosts):
        """Check that hosts are learned by FAUCET on the expected ports."""
        mac_ints_on_port_learned = {}
        for mac, port in hosts.items():
            self.mac_learned(mac)
            if port not in mac_ints_on_port_learned:
                mac_ints_on_port_learned[port] = set()
            macs_learned = self.macs_learned_on_port(port)
            mac_ints_on_port_learned[port].update(macs_learned)
        for mac, port in hosts.items():
            mac_int = self.mac_as_int(mac)
            self.assertTrue(mac_int in mac_ints_on_port_learned[port])

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        learned_mac_ports = {}
        learned_mac_ports[first_host.MAC()] = self.port_map['port_1']
        mac_intfs = []
        mac_ips = []

        for i in range(10, 16):
            if i == 14:
                first_host.cmd('fping -c3 %s' % ' '.join(mac_ips))
                # check first 4 are learnt
                self.verify_hosts_learned(learned_mac_ports)
                learned_mac_ports = {}
                mac_intfs = []
                mac_ips = []
                # wait for first lot to time out.
                # Adding 11 covers the random variation when a rule is added
                time.sleep(self.TIMEOUT + 11)
            mac_intf = 'mac%u' % i
            mac_intfs.append(mac_intf)
            mac_ipv4 = '10.0.0.%u' % i
            mac_ips.append(mac_ipv4)
            second_host.cmd('ip link add link %s %s type macvlan' % (
                second_host.defaultIntf(), mac_intf))
            second_host.cmd('ip address add %s/24 dev %s' % (
                mac_ipv4, mac_intf))
            address = second_host.cmd(
                '|'.join((
                    'ip link show %s' % mac_intf,
                    'grep -o "..:..:..:..:..:.."',
                    'head -1',
                    'xargs echo -n')))
            learned_mac_ports[address] = self.port_map['port_2']
            second_host.cmd('ip link set dev %s up' % mac_intf)

        first_host.cmd('fping -c3 %s' % ' '.join(mac_ips))
        learned_mac_ports[first_host.MAC()] = self.port_map['port_1']
        self.verify_hosts_learned(learned_mac_ports)
        # Verify same or less number of hosts on a port reported by Prometheus
        self.assertTrue((
            len(self.macs_learned_on_port(self.port_map['port_1'])) <=
            len(learned_mac_ports)))


class FaucetLearn50MACsOnPortTest(FaucetUntaggedTest):

    MAX_HOSTS = 50
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        self.ping_all_when_learned()
        mac_intf_ipv4s = []
        for i in range(10, 10+self.MAX_HOSTS):
            mac_intf_ipv4s.append(('mac%u' % i, '10.0.0.%u' % i))
        # configure macvlan interfaces and stimulate learning
        for mac_intf, mac_ipv4 in mac_intf_ipv4s:
            second_host.cmd('ip link add link %s %s type macvlan' % (
                second_host.defaultIntf(), mac_intf))
            second_host.cmd('ip address add %s/24 dev %s' % (
                mac_ipv4, mac_intf))
            second_host.cmd('ip link set dev %s up' % mac_intf)
            second_host.cmd('ping -c1 -I%s %s &' % (mac_intf, first_host.IP()))
        # verify connectivity
        for mac_intf, _ in mac_intf_ipv4s:
            self.one_ipv4_ping(
                second_host, first_host.IP(),
                require_host_learned=False, intf=mac_intf)
        # verify FAUCET thinks it learned this many hosts
        self.assertGreater(
            self.scrape_prometheus_var('vlan_hosts_learned', {'vlan': '100'}),
            self.MAX_HOSTS)


class FaucetUntaggedHUPTest(FaucetUntaggedTest):
    """Test handling HUP signal without config change."""

    def test_untagged(self):
        """Test that FAUCET receives HUP signal and keeps switching."""
        init_config_count = self.get_configure_count()
        for i in range(init_config_count, init_config_count+3):
            configure_count = self.get_configure_count()
            self.assertEquals(i, configure_count)
            self.verify_hup_faucet()
            configure_count = self.get_configure_count()
            self.assertTrue(i + 1, configure_count)
            self.assertEqual(
                self.scrape_prometheus_var('of_dp_disconnections', default=0),
                0)
            self.assertEqual(
                self.scrape_prometheus_var('of_dp_connections', default=0),
                1)
            self.wait_until_controller_flow()
            self.ping_all_when_learned()


class FaucetConfigReloadTest(FaucetTest):
    """Test handling HUP signal with config change."""

    N_UNTAGGED = 4
    N_TAGGED = 0
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"

    200:
        description: "untagged"
"""
    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""
    ACL = """
acls:
    1:
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5001
            actions:
                allow: 0
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5002
            actions:
                allow: 1
        - rule:
            actions:
                allow: 1
    2:
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5001
            actions:
                allow: 1
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5002
            actions:
                allow: 0
        - rule:
            actions:
                allow: 1
"""


    def setUp(self):
        super(FaucetConfigReloadTest, self).setUp()
        self.acl_config_file = '%s/acl.yaml' % self.tmpdir
        open(self.acl_config_file, 'w').write(self.ACL)
        open(self.faucet_config_path, 'a').write(
            'include:\n     - %s' % self.acl_config_file)
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid,
            n_tagged=self.N_TAGGED, n_untagged=self.N_UNTAGGED)
        self.start_net()

    def _get_conf(self):
        return yaml.load(open(self.faucet_config_path, 'r').read())

    def _reload_conf(self, conf, restart, cold_start, change_expected=True):
        open(self.faucet_config_path, 'w').write(yaml.dump(conf))
        if restart:
            var = 'faucet_config_reload_warm'
            if cold_start:
                var = 'faucet_config_reload_cold'
            old_count = int(
                self.scrape_prometheus_var(var, dpid=True, default=0))
            self.verify_hup_faucet()
            new_count = int(
                self.scrape_prometheus_var(var, dpid=True, default=0))
            if change_expected:
                self.assertEquals(
                    old_count + 1, new_count,
                    msg='%s did not increment: %u' % (var, new_count))
            else:
                self.assertEquals(
                    old_count, new_count,
                    msg='%s incremented: %u' % (var, new_count))

    def get_port_match_flow(self, port_no, table_id=3):
        flow = self.get_matching_flow_on_dpid(
            self.dpid, {u'in_port': int(port_no)}, table_id)
        return flow

    def test_add_unknown_dp(self):
        conf = self._get_conf()
        conf['dps']['unknown'] = {
            'dp_id': int(self.rand_dpid()),
            'hardware': 'Open vSwitch',
        }
        self._reload_conf(
            conf, restart=True, cold_start=False, change_expected=False)

    def change_port_config(self, port, config_name, config_value,
                           restart=True, conf=None, cold_start=False):
        if conf is None:
            conf = self._get_conf()
        conf['dps']['faucet-1']['interfaces'][port][config_name] = config_value
        self._reload_conf(conf, restart, cold_start)

    def change_vlan_config(self, vlan, config_name, config_value,
                           restart=True, conf=None, cold_start=False):
        if conf is None:
            conf = self._get_conf()
        conf['vlans'][vlan][config_name] = config_value
        self._reload_conf(conf, restart, cold_start)

    def test_port_change_vlan(self):
        first_host, second_host = self.net.hosts[:2]
        third_host, fourth_host = self.net.hosts[2:]

        self.ping_all_when_learned()
        self.change_port_config(
            self.port_map['port_1'], 'native_vlan', 200, restart=False)
        self.change_port_config(
            self.port_map['port_2'], 'native_vlan', 200, restart=True, cold_start=True)
        for port_name in ('port_1', 'port_2'):
            self.wait_until_matching_flow(
                {u'in_port': int(self.port_map[port_name])},
                table_id=1,
                actions=[u'SET_FIELD: {vlan_vid:4296}'])
        self.one_ipv4_ping(first_host, second_host.IP(), require_host_learned=False)
        # hosts 1 and 2 now in VLAN 200, so they shouldn't see floods for 3 and 4.
        self.verify_vlan_flood_limited(
            third_host, fourth_host, first_host)

    def test_port_change_acl(self):
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        orig_conf = self._get_conf()

        self.change_port_config(
            self.port_map['port_1'], 'acl_in', 1, cold_start=False)
        self.wait_until_matching_flow(
            {u'in_port': int(self.port_map['port_1']), u'tp_dst': 5001}, table_id=0)
        self.verify_tp_dst_blocked(5001, first_host, second_host)
        self.verify_tp_dst_notblocked(5002, first_host, second_host)

        self._reload_conf(orig_conf, True, cold_start=False)

        self.verify_tp_dst_notblocked(
            5001, first_host, second_host, table_id=None)
        self.verify_tp_dst_notblocked(
            5002, first_host, second_host, table_id=None)

    def test_port_change_permanent_learn(self):
        first_host, second_host, third_host = self.net.hosts[0:3]

        self.change_port_config(
            self.port_map['port_1'], 'permanent_learn', True, cold_start=False)
        self.ping_all_when_learned()
        original_third_host_mac = third_host.MAC()
        third_host.setMAC(first_host.MAC())
        self.assertEqual(100.0, self.net.ping((second_host, third_host)))
        self.assertEqual(0, self.net.ping((first_host, second_host)))
        third_host.setMAC(original_third_host_mac)
        self.ping_all_when_learned()
        self.change_port_config(
            self.port_map['port_1'], 'acl_in', 1, cold_start=False)
        self.wait_until_matching_flow(
            {u'in_port': int(self.port_map['port_1']), u'tp_dst': 5001},
            table_id=0)
        self.verify_tp_dst_blocked(5001, first_host, second_host)
        self.verify_tp_dst_notblocked(5002, first_host, second_host)


class FaucetUntaggedBGPIPv4DefaultRouteTest(FaucetUntaggedTest):
    """Test IPv4 routing and import default route from BGP."""

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["10.0.0.254/24"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["127.0.0.1"]
        bgp_neighbor_as: 2
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  router-id 2.2.2.2;
  neighbor 127.0.0.1 {
    local-address 127.0.0.1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
    static {
      route 0.0.0.0/0 next-hop 10.0.0.1 local-preference 100;
   }
 }
}
"""
    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        """Test IPv4 routing, and BGP routes received."""
        first_host, second_host = self.net.hosts[:2]
        first_host_alias_ip = ipaddress.ip_interface(u'10.99.99.99/24')
        first_host_alias_host_ip = ipaddress.ip_interface(
            ipaddress.ip_network(first_host_alias_ip.ip))
        self.host_ipv4_alias(first_host, first_host_alias_ip)
        self.wait_bgp_up('127.0.0.1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '4', 'vlan': '100'}),
            0)
        self.wait_exabgp_sent_updates(self.exabgp_log)
        self.add_host_route(
            second_host, first_host_alias_host_ip, self.FAUCET_VIPV4.ip)
        self.one_ipv4_ping(second_host, first_host_alias_ip.ip)
        self.one_ipv4_controller_ping(first_host)


class FaucetUntaggedBGPIPv4RouteTest(FaucetUntaggedTest):
    """Test IPv4 routing and import from BGP."""

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["10.0.0.254/24"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["127.0.0.1"]
        bgp_neighbor_as: 2
        routes:
            - route:
                ip_dst: 10.99.99.0/24
                ip_gw: 10.0.0.1
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  router-id 2.2.2.2;
  neighbor 127.0.0.1 {
    local-address 127.0.0.1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
    static {
      route 10.0.1.0/24 next-hop 10.0.0.1 local-preference 100;
      route 10.0.2.0/24 next-hop 10.0.0.2 local-preference 100;
      route 10.0.3.0/24 next-hop 10.0.0.2 local-preference 100;
      route 10.0.4.0/24 next-hop 10.0.0.254;
      route 10.0.5.0/24 next-hop 10.10.0.1;
   }
 }
}
"""
    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        """Test IPv4 routing, and BGP routes received."""
        first_host, second_host = self.net.hosts[:2]
        # wait until 10.0.0.1 has been resolved
        self.wait_for_route_as_flow(
            first_host.MAC(), ipaddress.IPv4Network(u'10.99.99.0/24'))
        self.wait_bgp_up('127.0.0.1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '4', 'vlan': '100'}),
            0)
        self.wait_exabgp_sent_updates(self.exabgp_log)
        self.verify_invalid_bgp_route('10.0.0.4/24 cannot be us')
        self.verify_invalid_bgp_route('10.0.0.5/24 is not a connected network')
        self.wait_for_route_as_flow(
            second_host.MAC(), ipaddress.IPv4Network(u'10.0.3.0/24'))
        self.verify_ipv4_routing_mesh()
        self.flap_all_switch_ports()
        self.verify_ipv4_routing_mesh()
        for host in first_host, second_host:
            self.one_ipv4_controller_ping(host)


class FaucetUntaggedIPv4RouteTest(FaucetUntaggedTest):
    """Test IPv4 routing and export to BGP."""

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["10.0.0.254/24"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["127.0.0.1"]
        bgp_neighbor_as: 2
        routes:
            - route:
                ip_dst: "10.0.1.0/24"
                ip_gw: "10.0.0.1"
            - route:
                ip_dst: "10.0.2.0/24"
                ip_gw: "10.0.0.2"
            - route:
                ip_dst: "10.0.3.0/24"
                ip_gw: "10.0.0.2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  process test {
    encoder json;
    neighbor-changes;
    receive-routes;
    run /bin/cat;
  }
  router-id 2.2.2.2;
  neighbor 127.0.0.1 {
    local-address 127.0.0.1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
  }
}
"""
    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        """Test IPv4 routing, and BGP routes sent."""
        self.verify_ipv4_routing_mesh()
        self.flap_all_switch_ports()
        self.verify_ipv4_routing_mesh()
        self.wait_bgp_up('127.0.0.1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '4', 'vlan': '100'}),
            0)
        # exabgp should have received our BGP updates
        updates = self.exabgp_updates(self.exabgp_log)
        assert re.search('10.0.0.0/24 next-hop 10.0.0.254', updates)
        assert re.search('10.0.1.0/24 next-hop 10.0.0.1', updates)
        assert re.search('10.0.2.0/24 next-hop 10.0.0.2', updates)
        assert re.search('10.0.2.0/24 next-hop 10.0.0.2', updates)


class FaucetZodiacUntaggedIPv4RouteTest(FaucetUntaggedIPv4RouteTest):

    RUN_GAUGE = False
    N_UNTAGGED = 3


class FaucetUntaggedVLanUnicastFloodTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: True
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.ping_all_when_learned()
        self.verify_port1_unicast(True)
        self.assertTrue(self.bogus_mac_flooded_to_port1())


class FaucetUntaggedNoVLanUnicastFloodTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.verify_port1_unicast(False)
        self.assertFalse(self.bogus_mac_flooded_to_port1())


class FaucetUntaggedPortUnicastFloodTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                unicast_flood: True
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.verify_port1_unicast(False)
        # VLAN level config to disable flooding takes precedence,
        # cannot enable port-only flooding.
        self.assertFalse(self.bogus_mac_flooded_to_port1())


class FaucetUntaggedNoPortUnicastFloodTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: True
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                unicast_flood: False
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.verify_port1_unicast(False)
        self.assertFalse(self.bogus_mac_flooded_to_port1())


class FaucetUntaggedHostMoveTest(FaucetUntaggedTest):

    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        self.assertEqual(0, self.net.ping((first_host, second_host)))
        self.swap_host_macs(first_host, second_host)
        self.net.ping((first_host, second_host))
        for host in (first_host, second_host):
            self.require_host_learned(host)
        self.assertEquals(0, self.net.ping((first_host, second_host)))


class FaucetUntaggedHostPermanentLearnTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                permanent_learn: True
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        self.ping_all_when_learned()
        first_host, second_host, third_host = self.net.hosts[0:3]
        # 3rd host impersonates 1st, 3rd host breaks but 1st host still OK
        original_third_host_mac = third_host.MAC()
        third_host.setMAC(first_host.MAC())
        self.assertEqual(100.0, self.net.ping((second_host, third_host)))
        self.assertEqual(0, self.net.ping((first_host, second_host)))
        # 3rd host stops impersonating, now everything fine again.
        third_host.setMAC(original_third_host_mac)
        self.ping_all_when_learned()



class FaucetSingleUntaggedIPv4ControlPlaneTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["10.0.0.254/24"]
"""

    CONFIG = """
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_ping_controller(self):
        first_host, second_host = self.net.hosts[0:2]
        for _ in range(5):
            self.one_ipv4_ping(first_host, second_host.IP())
            for host in first_host, second_host:
                self.one_ipv4_controller_ping(host)
            self.flap_all_switch_ports()

    def test_fping_controller(self):
        first_host = self.net.hosts[0]
        self.one_ipv4_controller_ping(first_host)
        self.verify_controller_fping(first_host, self.FAUCET_VIPV4)


class FaucetUntaggedIPv6RATest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fe80::1:254/64", "fc00::1:254/112", "fc00::2:254/112", "10.0.0.254/24"]
"""

    CONFIG = """
        advertise_interval: 5
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_ndisc6(self):
        first_host = self.net.hosts[0]
        for vip in ('fe80::1:254', 'fc00::1:254', 'fc00::2:254'):
            self.assertEquals(
                '0E:00:00:00:00:01',
                first_host.cmd('ndisc6 -q %s %s' % (vip, first_host.defaultIntf())).strip())

    def test_rdisc6(self):
        first_host = self.net.hosts[0]
        rdisc6_results = sorted(list(set(first_host.cmd(
            'rdisc6 -q %s' % first_host.defaultIntf()).splitlines())))
        self.assertEquals(
            ['fc00::1:0/112', 'fc00::2:0/112'],
            rdisc6_results)

    def test_ra_advertise(self):
        first_host = self.net.hosts[0]
        tcpdump_filter = ' and '.join((
            'ether dst 33:33:00:00:00:01',
            'ether src 0e:00:00:00:00:01',
            'icmp6',
            'ip6[40] == 134',
            'ip6 host fe80::1:254'))
        tcpdump_txt = self.tcpdump_helper(
            first_host, tcpdump_filter, [], timeout=30, vflags='-vv', packets=1)
        for ra_required in (
                r'fe80::1:254 > ff02::1:.+ICMP6, router advertisement',
                r'fc00::1:0/112, Flags \[onlink, auto\]',
                r'fc00::2:0/112, Flags \[onlink, auto\]',
                r'source link-address option \(1\), length 8 \(1\): 0e:00:00:00:00:01'):
            self.assertTrue(
                re.search(ra_required, tcpdump_txt),
                msg='%s: %s' % (ra_required, tcpdump_txt))

    def test_rs_reply(self):
        first_host = self.net.hosts[0]
        tcpdump_filter = ' and '.join((
            'ether src 0e:00:00:00:00:01',
            'ether dst %s' % first_host.MAC(),
            'icmp6',
            'ip6[40] == 134',
            'ip6 host fe80::1:254'))
        tcpdump_txt = self.tcpdump_helper(
            first_host, tcpdump_filter, [
                lambda: first_host.cmd(
                    'rdisc6 -1 %s' % first_host.defaultIntf())],
            timeout=30, vflags='-vv', packets=1)
        for ra_required in (
                r'fe80::1:254 > fe80::.+ICMP6, router advertisement',
                r'fc00::1:0/112, Flags \[onlink, auto\]',
                r'fc00::2:0/112, Flags \[onlink, auto\]',
                r'source link-address option \(1\), length 8 \(1\): 0e:00:00:00:00:01'):
            self.assertTrue(
                re.search(ra_required, tcpdump_txt),
                msg='%s: %s (%s)' % (ra_required, tcpdump_txt, tcpdump_filter))



class FaucetSingleUntaggedIPv6ControlPlaneTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/112"]
"""

    CONFIG = """
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_ping_controller(self):
        first_host, second_host = self.net.hosts[0:2]
        self.add_host_ipv6_address(first_host, 'fc00::1:1/112')
        self.add_host_ipv6_address(second_host, 'fc00::1:2/112')
        for _ in range(5):
            self.one_ipv6_ping(first_host, 'fc00::1:2')
            for host in first_host, second_host:
                self.one_ipv6_controller_ping(host)
            self.flap_all_switch_ports()

    def test_fping_controller(self):
        first_host = self.net.hosts[0]
        self.add_host_ipv6_address(first_host, 'fc00::1:1/112')
        self.one_ipv6_controller_ping(first_host)
        self.verify_controller_fping(first_host, self.FAUCET_VIPV6)


class FaucetTaggedAndUntaggedTest(FaucetTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
    101:
        description: "untagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                native_vlan: 101
                description: "b3"
            %(port_4)d:
                native_vlan: 101
                description: "b4"
"""

    def setUp(self):
        super(FaucetTaggedAndUntaggedTest, self).setUp()
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid, n_tagged=2, n_untagged=2)
        self.start_net()

    def test_seperate_untagged_tagged(self):
        tagged_host_pair = self.net.hosts[:2]
        untagged_host_pair = self.net.hosts[2:]
        self.verify_vlan_flood_limited(
            tagged_host_pair[0], tagged_host_pair[1], untagged_host_pair[0])
        self.verify_vlan_flood_limited(
            untagged_host_pair[0], untagged_host_pair[1], tagged_host_pair[0])
        # hosts within VLANs can ping each other
        self.assertEquals(0, self.net.ping(tagged_host_pair))
        self.assertEquals(0, self.net.ping(untagged_host_pair))
        # hosts cannot ping hosts in other VLANs
        self.assertEquals(
            100, self.net.ping([tagged_host_pair[0], untagged_host_pair[0]]))


class FaucetUntaggedACLTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
acls:
    1:
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5001
            actions:
                allow: 0
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5002
            actions:
                allow: 1
        - rule:
            actions:
                allow: 1
"""
    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_port5001_blocked(self):
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_blocked(5001, first_host, second_host)

    def test_port5002_notblocked(self):
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_notblocked(5002, first_host, second_host)


class FaucetUntaggedVLANACLTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
acls:
    1:
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5001
            actions:
                allow: 0
        - rule:
            dl_type: 0x800
            nw_proto: 6
            tp_dst: 5002
            actions:
                allow: 1
        - rule:
            actions:
                allow: 1
vlans:
    100:
        description: "untagged"
        acl_in: 1
"""
    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_port5001_blocked(self):
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_blocked(
            5001, first_host, second_host, table_id=2)

    def test_port5002_notblocked(self):
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_notblocked(
            5002, first_host, second_host, table_id=2)


class FaucetZodiacUntaggedACLTest(FaucetUntaggedACLTest):

    RUN_GAUGE = False
    N_UNTAGGED = 3

    def test_untagged(self):
        """All hosts on the same untagged VLAN should have connectivity."""
        self.ping_all_when_learned()
        self.flap_all_switch_ports()
        self.ping_all_when_learned()


class FaucetUntaggedACLMirrorTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
acls:
    1:
        - rule:
            actions:
                allow: 1
                mirror: mirrorport
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            %(port_2)d:
                native_vlan: 100
                description: "b2"
                acl_in: 1
            mirrorport:
                number: %(port_3)d
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host, mirror_host = self.net.hosts[0:3]
        self.verify_ping_mirrored(first_host, second_host, mirror_host)

    def test_eapol_mirrored(self):
        first_host, second_host, mirror_host = self.net.hosts[0:3]
        self.verify_eapol_mirrored(first_host, second_host, mirror_host)


class FaucetZodiacUntaggedACLMirrorTest(FaucetUntaggedACLMirrorTest):

    RUN_GAUGE = False
    N_UNTAGGED = 3


class FaucetUntaggedACLMirrorDefaultAllowTest(FaucetUntaggedACLMirrorTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
acls:
    1:
        - rule:
            actions:
                mirror: mirrorport
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            %(port_2)d:
                native_vlan: 100
                description: "b2"
                acl_in: 1
            mirrorport:
                number: %(port_3)d
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""


class FaucetUntaggedOutputTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
acls:
    1:
        - rule:
            dl_dst: "01:02:03:04:05:06"
            actions:
                output:
                    dl_dst: "06:06:06:06:06:06"
                    vlan_vid: 123
                    port: acloutport
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            acloutport:
                number: %(port_2)d
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        # we expected to see the rewritten address and VLAN
        tcpdump_filter = ('icmp and ether dst 06:06:06:06:06:06')
        tcpdump_txt = self.tcpdump_helper(
            second_host, tcpdump_filter, [
                lambda: first_host.cmd(
                    'arp -s %s %s' % (second_host.IP(), '01:02:03:04:05:06')),
                lambda: first_host.cmd('ping -c1 %s' % second_host.IP())])
        self.assertTrue(re.search(
            '%s: ICMP echo request' % second_host.IP(), tcpdump_txt))
        self.assertTrue(re.search(
            'vlan 123', tcpdump_txt))


class FaucetUntaggedMultiVlansOutputTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
acls:
    1:
        - rule:
            dl_dst: "01:02:03:04:05:06"
            actions:
                output:
                    dl_dst: "06:06:06:06:06:06"
                    vlan_vids: [123, 456]
                    port: acloutport
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            acloutport:
                number: %(port_2)d
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    @unittest.skip('needs OVS dev or > v2.8')
    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        # we expected to see the rewritten address and VLAN
        tcpdump_filter = 'vlan'
        tcpdump_txt = self.tcpdump_helper(
            second_host, tcpdump_filter, [
                lambda: first_host.cmd(
                    'arp -s %s %s' % (second_host.IP(), '01:02:03:04:05:06')),
                lambda: first_host.cmd('ping -c1 %s' % second_host.IP())])
        self.assertTrue(re.search(
            '%s: ICMP echo request' % second_host.IP(), tcpdump_txt))
        self.assertTrue(re.search(
            'vlan 456.+vlan 123', tcpdump_txt))


class FaucetUntaggedMirrorTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        unicast_flood: False
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
                mirror: %(port_1)d
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host, mirror_host = self.net.hosts[0:3]
        self.verify_ping_mirrored(first_host, second_host, mirror_host)


class FaucetTaggedTest(FaucetTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def setUp(self):
        super(FaucetTaggedTest, self).setUp()
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid, n_tagged=4)
        self.start_net()

    def test_tagged(self):
        self.ping_all_when_learned()


class FaucetTaggedPopVlansOutputTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        unicast_flood: False
acls:
    1:
        - rule:
            vlan_vid: 100
            dl_dst: "01:02:03:04:05:06"
            actions:
                output:
                    dl_dst: "06:06:06:06:06:06"
                    pop_vlans: 1
                    port: acloutport
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
                acl_in: 1
            acloutport:
                tagged_vlans: [100]
                number: %(port_2)d
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_tagged(self):
        first_host, second_host = self.net.hosts[0:2]
        tcpdump_filter = 'not vlan and icmp and ether dst 06:06:06:06:06:06'
        tcpdump_txt = self.tcpdump_helper(
            second_host, tcpdump_filter, [
                lambda: first_host.cmd(
                    'arp -s %s %s' % (second_host.IP(), '01:02:03:04:05:06')),
                lambda: first_host.cmd(
                    'ping -c1 %s' % second_host.IP())], packets=10, root_intf=True)
        self.assertTrue(re.search(
            '%s: ICMP echo request' % second_host.IP(), tcpdump_txt))


class FaucetTaggedIPv4ControlPlaneTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["10.0.0.254/24"]
"""

    CONFIG = """
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_ping_controller(self):
        first_host, second_host = self.net.hosts[0:2]
        self.one_ipv4_ping(first_host, second_host.IP())
        for host in first_host, second_host:
            self.one_ipv4_controller_ping(host)


class FaucetTaggedIPv6ControlPlaneTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["fc00::1:254/112"]
"""

    CONFIG = """
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_ping_controller(self):
        first_host, second_host = self.net.hosts[0:2]
        self.add_host_ipv6_address(first_host, 'fc00::1:1/112')
        self.add_host_ipv6_address(second_host, 'fc00::1:2/112')
        self.one_ipv6_ping(first_host, 'fc00::1:2')
        for host in first_host, second_host:
            self.one_ipv6_controller_ping(host)


class FaucetTaggedIPv4RouteTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["10.0.0.254/24"]
        routes:
            - route:
                ip_dst: "10.0.1.0/24"
                ip_gw: "10.0.0.1"
            - route:
                ip_dst: "10.0.2.0/24"
                ip_gw: "10.0.0.2"
            - route:
                ip_dst: "10.0.3.0/24"
                ip_gw: "10.0.0.2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_tagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_routed_ip = ipaddress.ip_interface(u'10.0.1.1/24')
        second_host_routed_ip = ipaddress.ip_interface(u'10.0.2.1/24')
        for _ in range(3):
            self.verify_ipv4_routing(
                first_host, first_host_routed_ip,
                second_host, second_host_routed_ip)
            self.swap_host_macs(first_host, second_host)


class FaucetTaggedProactiveNeighborIPv4RouteTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["10.0.0.254/24"]
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        proactive_learn: true
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_tagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_alias_ip = ipaddress.ip_interface(u'10.0.0.99/24')
        first_host_alias_host_ip = ipaddress.ip_interface(
            ipaddress.ip_network(first_host_alias_ip.ip))
        self.host_ipv4_alias(first_host, first_host_alias_ip)
        self.add_host_route(second_host, first_host_alias_host_ip, self.FAUCET_VIPV4.ip)
        self.one_ipv4_ping(second_host, first_host_alias_ip.ip)
        self.assertGreater(
            self.scrape_prometheus_var(
                'vlan_neighbors', {'ipv': '4', 'vlan': '100'}),
            1)


class FaucetTaggedProactiveNeighborIPv6RouteTest(FaucetTaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["fc00::1:3/64"]
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        proactive_learn: true
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_tagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_alias_ip = ipaddress.ip_interface(u'fc00::1:99/64')
        faucet_vip_ip = ipaddress.ip_interface(u'fc00::1:3/126')
        first_host_alias_host_ip = ipaddress.ip_interface(
            ipaddress.ip_network(first_host_alias_ip.ip))
        self.add_host_ipv6_address(first_host, ipaddress.ip_interface(u'fc00::1:1/64'))
        # We use a narrower mask to force second_host to use the /128 route,
        # since otherwise it would realize :99 is directly connected via ND and send direct.
        self.add_host_ipv6_address(second_host, ipaddress.ip_interface(u'fc00::1:2/126'))
        self.add_host_ipv6_address(first_host, first_host_alias_ip)
        self.add_host_route(second_host, first_host_alias_host_ip, faucet_vip_ip.ip)
        self.one_ipv6_ping(second_host, first_host_alias_ip.ip)
        self.assertGreater(
            self.scrape_prometheus_var(
                'vlan_neighbors', {'ipv': '6', 'vlan': '100'}),
            1)


class FaucetUntaggedIPv4InterVLANRouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "100"
        faucet_vips: ["10.100.0.254/24"]
    200:
        description: "200"
        faucet_vips: ["10.200.0.254/24"]
routers:
    router-1:
        vlans: [100, 200]
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 200
                description: "b2"
            %(port_3)d:
                native_vlan: 200
                description: "b3"
            %(port_4)d:
                native_vlan: 200
                description: "b4"
"""

    def test_untagged(self):
        first_host_ip = ipaddress.ip_interface(u'10.100.0.1/24')
        first_faucet_vip = ipaddress.ip_interface(u'10.100.0.254/24')
        second_host_ip = ipaddress.ip_interface(u'10.200.0.1/24')
        second_faucet_vip = ipaddress.ip_interface(u'10.200.0.254/24')
        first_host, second_host = self.net.hosts[:2]
        first_host.setIP(str(first_host_ip.ip))
        second_host.setIP(str(second_host_ip.ip))
        self.add_host_route(first_host, second_host_ip, first_faucet_vip.ip)
        self.add_host_route(second_host, first_host_ip, second_faucet_vip.ip)
        self.one_ipv4_ping(first_host, first_faucet_vip.ip)
        self.one_ipv4_ping(second_host, second_faucet_vip.ip)
        self.one_ipv4_ping(first_host, second_host_ip.ip)
        self.one_ipv4_ping(second_host, first_host_ip.ip)


class FaucetUntaggedMixedIPv4RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["172.16.0.254/24", "10.0.0.254/24"]
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_net = ipaddress.ip_interface(u'10.0.0.1/24')
        second_host_net = ipaddress.ip_interface(u'172.16.0.1/24')
        second_host.setIP(str(second_host_net.ip))
        self.one_ipv4_ping(first_host, self.FAUCET_VIPV4.ip)
        self.one_ipv4_ping(second_host, self.FAUCET_VIPV4_2.ip)
        self.add_host_route(
            first_host, second_host_net, self.FAUCET_VIPV4.ip)
        self.add_host_route(
            second_host, first_host_net, self.FAUCET_VIPV4_2.ip)
        self.one_ipv4_ping(first_host, second_host_net.ip)
        self.one_ipv4_ping(second_host, first_host_net.ip)


class FaucetUntaggedMixedIPv6RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/64", "fc01::1:254/64"]
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_net = ipaddress.ip_interface(u'fc00::1:1/64')
        second_host_net = ipaddress.ip_interface(u'fc01::1:1/64')
        self.add_host_ipv6_address(first_host, first_host_net)
        self.one_ipv6_ping(first_host, self.FAUCET_VIPV6.ip)
        self.add_host_ipv6_address(second_host, second_host_net)
        self.one_ipv6_ping(second_host, self.FAUCET_VIPV6_2.ip)
        self.add_host_route(
            first_host, second_host_net, self.FAUCET_VIPV6.ip)
        self.add_host_route(
            second_host, first_host_net, self.FAUCET_VIPV6_2.ip)
        self.one_ipv6_ping(first_host, second_host_net.ip)
        self.one_ipv6_ping(second_host, first_host_net.ip)


class FaucetUntaggedBGPIPv6DefaultRouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/112"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["::1"]
        bgp_neighbor_as: 2
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  router-id 2.2.2.2;
  neighbor ::1 {
    local-address ::1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
    static {
      route ::/0 next-hop fc00::1:1 local-preference 100;
    }
  }
}
"""

    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        self.add_host_ipv6_address(first_host, 'fc00::1:1/112')
        self.add_host_ipv6_address(second_host, 'fc00::1:2/112')
        first_host_alias_ip = ipaddress.ip_interface(u'fc00::50:1/112')
        first_host_alias_host_ip = ipaddress.ip_interface(
            ipaddress.ip_network(first_host_alias_ip.ip))
        self.add_host_ipv6_address(first_host, first_host_alias_ip)
        self.wait_bgp_up('::1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '6', 'vlan': '100'}),
            0)
        self.wait_exabgp_sent_updates(self.exabgp_log)
        self.add_host_route(
            second_host, first_host_alias_host_ip, self.FAUCET_VIPV6.ip)
        self.one_ipv6_ping(second_host, first_host_alias_ip.ip)
        self.one_ipv6_controller_ping(first_host)


class FaucetUntaggedBGPIPv6RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/112"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["::1"]
        bgp_neighbor_as: 2
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  router-id 2.2.2.2;
  neighbor ::1 {
    local-address ::1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
    static {
      route fc00::10:1/112 next-hop fc00::1:1 local-preference 100;
      route fc00::20:1/112 next-hop fc00::1:2 local-preference 100;
      route fc00::30:1/112 next-hop fc00::1:2 local-preference 100;
      route fc00::40:1/112 next-hop fc00::1:254;
      route fc00::50:1/112 next-hop fc00::2:2;
    }
  }
}
"""
    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        self.wait_bgp_up('::1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '6', 'vlan': '100'}),
            0)
        self.wait_exabgp_sent_updates(self.exabgp_log)
        self.verify_invalid_bgp_route('fc00::40:1/112 cannot be us')
        self.verify_invalid_bgp_route('fc00::50:1/112 is not a connected network')
        self.verify_ipv6_routing_mesh()
        self.flap_all_switch_ports()
        self.verify_ipv6_routing_mesh()
        for host in first_host, second_host:
            self.one_ipv6_controller_ping(host)


class FaucetUntaggedSameVlanIPv6RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::10:1/112", "fc00::20:1/112"]
        routes:
            - route:
                ip_dst: "fc00::10:0/112"
                ip_gw: "fc00::10:2"
            - route:
                ip_dst: "fc00::20:0/112"
                ip_gw: "fc00::20:2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[:2]
        first_host_ip = ipaddress.ip_interface(u'fc00::10:2/112')
        first_host_ctrl_ip = ipaddress.ip_address(u'fc00::10:1')
        second_host_ip = ipaddress.ip_interface(u'fc00::20:2/112')
        second_host_ctrl_ip = ipaddress.ip_address(u'fc00::20:1')
        self.add_host_ipv6_address(first_host, first_host_ip)
        self.add_host_ipv6_address(second_host, second_host_ip)
        self.add_host_route(
            first_host, second_host_ip, first_host_ctrl_ip)
        self.add_host_route(
            second_host, first_host_ip, second_host_ctrl_ip)
        self.wait_for_route_as_flow(
            first_host.MAC(), first_host_ip.network)
        self.wait_for_route_as_flow(
            second_host.MAC(), second_host_ip.network)
        self.one_ipv6_ping(first_host, second_host_ip.ip)
        self.one_ipv6_ping(first_host, second_host_ctrl_ip)
        self.one_ipv6_ping(second_host, first_host_ip.ip)
        self.one_ipv6_ping(second_host, first_host_ctrl_ip)


class FaucetUntaggedIPv6RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/112"]
        bgp_port: %(bgp_port)d
        bgp_as: 1
        bgp_routerid: "1.1.1.1"
        bgp_neighbor_addresses: ["::1"]
        bgp_neighbor_as: 2
        routes:
            - route:
                ip_dst: "fc00::10:0/112"
                ip_gw: "fc00::1:1"
            - route:
                ip_dst: "fc00::20:0/112"
                ip_gw: "fc00::1:2"
            - route:
                ip_dst: "fc00::30:0/112"
                ip_gw: "fc00::1:2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    exabgp_conf = """
group test {
  process test {
    encoder json;
    neighbor-changes;
    receive-routes;
    run /bin/cat;
  }
  router-id 2.2.2.2;
  neighbor ::1 {
    local-address ::1;
    connect %(bgp_port)d;
    peer-as 1;
    local-as 2;
  }
}
"""
    exabgp_log = None

    def pre_start_net(self):
        self.exabgp_log = self.start_exabgp(self.exabgp_conf)

    def test_untagged(self):
        self.verify_ipv6_routing_mesh()
        second_host = self.net.hosts[1]
        self.flap_all_switch_ports()
        self.wait_for_route_as_flow(
            second_host.MAC(), ipaddress.IPv6Network(u'fc00::30:0/112'))
        self.verify_ipv6_routing_mesh()
        self.wait_bgp_up('::1', 100)
        self.assertGreater(
            self.scrape_prometheus_var(
                'bgp_neighbor_routes', {'ipv': '6', 'vlan': '100'}),
            0)
        updates = self.exabgp_updates(self.exabgp_log)
        assert re.search('fc00::1:0/112 next-hop fc00::1:254', updates)
        assert re.search('fc00::10:0/112 next-hop fc00::1:1', updates)
        assert re.search('fc00::20:0/112 next-hop fc00::1:2', updates)
        assert re.search('fc00::30:0/112 next-hop fc00::1:2', updates)


class FaucetTaggedIPv6RouteTest(FaucetTaggedTest):
    """Test basic IPv6 routing without BGP."""

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "tagged"
        faucet_vips: ["fc00::1:254/112"]
        routes:
            - route:
                ip_dst: "fc00::10:0/112"
                ip_gw: "fc00::1:1"
            - route:
                ip_dst: "fc00::20:0/112"
                ip_gw: "fc00::1:2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        interfaces:
            %(port_1)d:
                tagged_vlans: [100]
                description: "b1"
            %(port_2)d:
                tagged_vlans: [100]
                description: "b2"
            %(port_3)d:
                tagged_vlans: [100]
                description: "b3"
            %(port_4)d:
                tagged_vlans: [100]
                description: "b4"
"""

    def test_tagged(self):
        """Test IPv6 routing works."""
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_ip = ipaddress.ip_interface(u'fc00::1:1/112')
        second_host_ip = ipaddress.ip_interface(u'fc00::1:2/112')
        first_host_routed_ip = ipaddress.ip_interface(u'fc00::10:1/112')
        second_host_routed_ip = ipaddress.ip_interface(u'fc00::20:1/112')
        for _ in range(5):
            self.verify_ipv6_routing_pair(
                first_host, first_host_ip, first_host_routed_ip,
                second_host, second_host_ip, second_host_routed_ip)
            self.swap_host_macs(first_host, second_host)


class FaucetStringOfDPTest(FaucetTest):

    NUM_HOSTS = 4
    VID = 100
    dpids = None

    def build_net(self, stack=False, n_dps=1,
                  n_tagged=0, tagged_vid=100,
                  n_untagged=0, untagged_vid=100,
                  include=[], include_optional=[], acls={}, acl_in_dp={}):
        """Set up Mininet and Faucet for the given topology."""
        self.dpids = [str(self.rand_dpid()) for _ in range(n_dps)]
        self.dpid = self.dpids[0]
        self.CONFIG = self.get_config(
            self.dpids,
            stack,
            self.hardware,
            self.debug_log_path,
            n_tagged,
            tagged_vid,
            n_untagged,
            untagged_vid,
            include,
            include_optional,
            acls,
            acl_in_dp,
        )
        open(self.faucet_config_path, 'w').write(self.CONFIG)
        self.topo = faucet_mininet_test_topo.FaucetStringOfDPSwitchTopo(
            self.ports_sock,
            dpids=self.dpids,
            n_tagged=n_tagged,
            tagged_vid=tagged_vid,
            n_untagged=n_untagged,
            test_name=self._test_name(),
        )

    def get_config(self, dpids=[], stack=False, hardware=None, ofchannel_log=None,
                   n_tagged=0, tagged_vid=0, n_untagged=0, untagged_vid=0,
                   include=[], include_optional=[], acls={}, acl_in_dp={}):
        """Build a complete Faucet configuration for each datapath, using the given topology."""

        def dp_name(i):
            return 'faucet-%i' % (i + 1)

        def add_vlans(n_tagged, tagged_vid, n_untagged, untagged_vid):
            vlans_config = {}
            if n_untagged:
                vlans_config[untagged_vid] = {
                    'description': 'untagged',
                }

            if ((n_tagged and not n_untagged) or
                    (n_tagged and n_untagged and tagged_vid != untagged_vid)):
                vlans_config[tagged_vid] = {
                    'description': 'tagged',
                }
            return vlans_config

        def add_acl_to_port(name, port, interfaces_config):
            if name in acl_in_dp and port in acl_in_dp[name]:
                interfaces_config[port]['acl_in'] = acl_in_dp[name][port]

        def add_dp_to_dp_ports(dp_config, port, interfaces_config, i,
                               dpid_count, stack, n_tagged, tagged_vid,
                               n_untagged, untagged_vid):
            # Add configuration for the switch-to-switch links
            # (0 for a single switch, 1 for an end switch, 2 for middle switches).
            first_dp = i == 0
            second_dp = i == 1
            last_dp = i == dpid_count - 1
            end_dp = first_dp or last_dp
            num_switch_links = 0
            if dpid_count > 1:
                if end_dp:
                    num_switch_links = 1
                else:
                    num_switch_links = 2

            if stack and first_dp:
                dp_config['stack'] = {
                    'priority': 1
                }

            first_stack_port = port

            for stack_dp_port in range(num_switch_links):
                tagged_vlans = None

                peer_dp = None
                if stack_dp_port == 0:
                    if first_dp:
                        peer_dp = i + 1
                    else:
                        peer_dp = i - 1
                    if first_dp or second_dp:
                        peer_port = first_stack_port
                    else:
                        peer_port = first_stack_port + 1
                else:
                    peer_dp = i + 1
                    peer_port = first_stack_port

                description = 'to %s' % dp_name(peer_dp)

                interfaces_config[port] = {
                    'description': description,
                }

                if stack:
                    interfaces_config[port]['stack'] = {
                        'dp': dp_name(peer_dp),
                        'port': peer_port,
                    }
                else:
                    if n_tagged and n_untagged and n_tagged != n_untagged:
                        tagged_vlans = [tagged_vid, untagged_vid]
                    elif ((n_tagged and not n_untagged) or
                          (n_tagged and n_untagged and tagged_vid == untagged_vid)):
                        tagged_vlans = [tagged_vid]
                    elif n_untagged and not n_tagged:
                        tagged_vlans = [untagged_vid]

                    if tagged_vlans:
                        interfaces_config[port]['tagged_vlans'] = tagged_vlans

                add_acl_to_port(name, port, interfaces_config)
                port += 1

        def add_dp(name, dpid, i, dpid_count, stack,
                   n_tagged, tagged_vid, n_untagged, untagged_vid):
            dpid_ofchannel_log = ofchannel_log + str(i)
            dp_config = {
                'dp_id': int(dpid),
                'hardware': hardware,
                'ofchannel_log': dpid_ofchannel_log,
                'interfaces': {},
            }
            interfaces_config = dp_config['interfaces']

            port = 1
            for _ in range(n_tagged):
                interfaces_config[port] = {
                    'tagged_vlans': [tagged_vid],
                    'description': 'b%i' % port,
                }
                add_acl_to_port(name, port, interfaces_config)
                port += 1

            for _ in range(n_untagged):
                interfaces_config[port] = {
                    'native_vlan': untagged_vid,
                    'description': 'b%i' % port,
                }
                add_acl_to_port(name, port, interfaces_config)
                port += 1

            add_dp_to_dp_ports(
                dp_config, port, interfaces_config, i, dpid_count, stack,
                n_tagged, tagged_vid, n_untagged, untagged_vid)

            return dp_config

        config = {'version': 2}

        if include:
            config['include'] = list(include)

        if include_optional:
            config['include-optional'] = list(include_optional)

        config['vlans'] = add_vlans(
            n_tagged, tagged_vid, n_untagged, untagged_vid)

        config['acls'] = acls.copy()

        dpid_count = len(dpids)
        config['dps'] = {}

        for i, dpid in enumerate(dpids):
            name = dp_name(i)
            config['dps'][name] = add_dp(
                name, dpid, i, dpid_count, stack,
                n_tagged, tagged_vid, n_untagged, untagged_vid)

        return yaml.dump(config, default_flow_style=False)

    def matching_flow_present(self, match, timeout=10, table_id=None,
                              actions=None, match_exact=None):
        """Find the first DP that has a flow that matches match."""
        for dpid in self.dpids:
            if self.matching_flow_present_on_dpid(
                    dpid, match, timeout=timeout,
                    table_id=table_id, actions=actions,
                    match_exact=match_exact):
                return True
        return False

    def eventually_all_reachable(self, retries=3):
        """Allow time for distributed learning to happen."""
        for _ in range(retries):
            loss = self.net.pingAll()
            if loss == 0:
                break
        self.assertEquals(0, loss)


class FaucetStringOfDPUntaggedTest(FaucetStringOfDPTest):

    NUM_DPS = 3

    def setUp(self):
        super(FaucetStringOfDPUntaggedTest, self).setUp()
        self.build_net(
            n_dps=self.NUM_DPS, n_untagged=self.NUM_HOSTS, untagged_vid=self.VID)
        self.start_net()

    def test_untagged(self):
        """All untagged hosts in multi switch topology can reach one another."""
        self.assertEquals(0, self.net.pingAll())


class FaucetStringOfDPTaggedTest(FaucetStringOfDPTest):

    NUM_DPS = 3

    def setUp(self):
        super(FaucetStringOfDPTaggedTest, self).setUp()
        self.build_net(
            n_dps=self.NUM_DPS, n_tagged=self.NUM_HOSTS, tagged_vid=self.VID)
        self.start_net()

    def test_tagged(self):
        """All tagged hosts in multi switch topology can reach one another."""
        self.assertEquals(0, self.net.pingAll())


class FaucetStackStringOfDPTaggedTest(FaucetStringOfDPTest):
    """Test topology of stacked datapaths with tagged hosts."""

    NUM_DPS = 3

    def setUp(self):
        super(FaucetStackStringOfDPTaggedTest, self).setUp()
        self.build_net(
            stack=True,
            n_dps=self.NUM_DPS,
            n_tagged=self.NUM_HOSTS,
            tagged_vid=self.VID)
        self.start_net()

    def test_tagged(self):
        """All tagged hosts in stack topology can reach each other."""
        self.eventually_all_reachable()


class FaucetStackStringOfDPUntaggedTest(FaucetStringOfDPTest):
    """Test topology of stacked datapaths with tagged hosts."""

    NUM_DPS = 2
    NUM_HOSTS = 2

    def setUp(self):
        super(FaucetStackStringOfDPUntaggedTest, self).setUp()
        self.build_net(
            stack=True,
            n_dps=self.NUM_DPS,
            n_untagged=self.NUM_HOSTS,
            untagged_vid=self.VID)
        self.start_net()

    def test_untagged(self):
        """All untagged hosts in stack topology can reach each other."""
        self.eventually_all_reachable()


class FaucetStringOfDPACLOverrideTest(FaucetStringOfDPTest):

    NUM_DPS = 1
    NUM_HOSTS = 2

    # ACL rules which will get overridden.
    ACLS = {
        1: [
            {'rule': {
                'dl_type': int('0x800', 16),
                'nw_proto': 6,
                'tp_dst': 5001,
                'actions': {
                    'allow': 1,
                },
            }},
            {'rule': {
                'dl_type': int('0x800', 16),
                'nw_proto': 6,
                'tp_dst': 5002,
                'actions': {
                    'allow': 0,
                },
            }},
            {'rule': {
                'actions': {
                    'allow': 1,
                },
            }},
        ],
    }

    # ACL rules which get put into an include-optional
    # file, then reloaded into FAUCET.
    ACLS_OVERRIDE = {
        1: [
            {'rule': {
                'dl_type': int('0x800', 16),
                'nw_proto': 6,
                'tp_dst': 5001,
                'actions': {
                    'allow': 0,
                },
            }},
            {'rule': {
                'dl_type': int('0x800', 16),
                'nw_proto': 6,
                'tp_dst': 5002,
                'actions': {
                    'allow': 1,
                },
            }},
            {'rule': {
                'actions': {
                    'allow': 1,
                },
            }},
        ],
    }

    # DP-to-acl_in port mapping.
    ACL_IN_DP = {
        'faucet-1': {
            # Port 1, acl_in = 1
            1: 1,
        },
    }

    def setUp(self):
        super(FaucetStringOfDPACLOverrideTest, self).setUp()
        self.acls_config = os.path.join(self.tmpdir, 'acls.yaml')
        self.build_net(
            n_dps=self.NUM_DPS,
            n_untagged=self.NUM_HOSTS,
            untagged_vid=self.VID,
            include_optional=[self.acls_config],
            acls=self.ACLS,
            acl_in_dp=self.ACL_IN_DP,
        )
        self.start_net()

    def test_port5001_blocked(self):
        """Test that TCP port 5001 is blocked."""
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_notblocked(5001, first_host, second_host)
        open(self.acls_config, 'w').write(self.get_config(acls=self.ACLS_OVERRIDE))
        self.verify_hup_faucet()
        self.verify_tp_dst_blocked(5001, first_host, second_host)

    def test_port5002_notblocked(self):
        """Test that TCP port 5002 is not blocked."""
        self.ping_all_when_learned()
        first_host, second_host = self.net.hosts[0:2]
        self.verify_tp_dst_blocked(5002, first_host, second_host)
        open(self.acls_config, 'w').write(self.get_config(acls=self.ACLS_OVERRIDE))
        self.verify_hup_faucet()
        self.verify_tp_dst_notblocked(5002, first_host, second_host)


class FaucetGroupTableTest(FaucetUntaggedTest):
    CONFIG = """
        group_table: True
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_group_exist(self):
        self.assertEqual(
            100,
            self.get_group_id_for_matching_flow(
                {u'dl_vlan': u'100', u'dl_dst': u'ff:ff:ff:ff:ff:ff'},
                table_id=7))


class FaucetGroupTableUntaggedIPv4RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["10.0.0.254/24"]
        routes:
            - route:
                ip_dst: "10.0.1.0/24"
                ip_gw: "10.0.0.1"
            - route:
                ip_dst: "10.0.2.0/24"
                ip_gw: "10.0.0.2"
            - route:
                ip_dst: "10.0.3.0/24"
                ip_gw: "10.0.0.2"
"""
    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        group_table: True
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_routed_ip = ipaddress.ip_interface(u'10.0.1.1/24')
        second_host_routed_ip = ipaddress.ip_interface(u'10.0.2.1/24')
        self.verify_ipv4_routing(
            first_host, first_host_routed_ip,
            second_host, second_host_routed_ip,
            with_group_table=True)
        self.swap_host_macs(first_host, second_host)
        self.verify_ipv4_routing(
            first_host, first_host_routed_ip,
            second_host, second_host_routed_ip,
            with_group_table=True)


class FaucetGroupUntaggedIPv6RouteTest(FaucetUntaggedTest):

    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
        faucet_vips: ["fc00::1:254/112"]
        routes:
            - route:
                ip_dst: "fc00::10:0/112"
                ip_gw: "fc00::1:1"
            - route:
                ip_dst: "fc00::20:0/112"
                ip_gw: "fc00::1:2"
            - route:
                ip_dst: "fc00::30:0/112"
                ip_gw: "fc00::1:2"
"""

    CONFIG = """
        arp_neighbor_timeout: 2
        max_resolve_backoff_time: 1
        group_table: True
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        host_pair = self.net.hosts[:2]
        first_host, second_host = host_pair
        first_host_ip = ipaddress.ip_interface(u'fc00::1:1/112')
        second_host_ip = ipaddress.ip_interface(u'fc00::1:2/112')
        first_host_routed_ip = ipaddress.ip_interface(u'fc00::10:1/112')
        second_host_routed_ip = ipaddress.ip_interface(u'fc00::20:1/112')
        self.verify_ipv6_routing_pair(
            first_host, first_host_ip, first_host_routed_ip,
            second_host, second_host_ip, second_host_routed_ip,
            with_group_table=True)
        self.swap_host_macs(first_host, second_host)
        self.verify_ipv6_routing_pair(
            first_host, first_host_ip, first_host_routed_ip,
            second_host, second_host_ip, second_host_routed_ip,
            with_group_table=True)


class FaucetEthSrcMaskTest(FaucetUntaggedTest):
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"

acls:
    1:
        - rule:
            eth_src: 0e:0d:00:00:00:00/ff:ff:00:00:00:00
            actions:
                allow: 1
        - rule:
            actions:
                allow: 0
"""
    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        first_host.setMAC('0e:0d:00:00:00:99')
        self.assertEqual(0, self.net.ping((first_host, second_host)))
        self.wait_nonzero_packet_count_flow(
            {u'dl_src': u'0e:0d:00:00:00:00/ff:ff:00:00:00:00'}, table_id=0)


class FaucetDestRewriteTest(FaucetUntaggedTest):
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"

acls:
    1:
        - rule:
            dl_dst: "00:00:00:00:00:02"
            actions:
                allow: 1
                output:
                    dl_dst: "00:00:00:00:00:03"
        - rule:
            actions:
                allow: 1
"""
    CONFIG = """
        interfaces:
            %(port_1)d:
                native_vlan: 100
                description: "b1"
                acl_in: 1
            %(port_2)d:
                native_vlan: 100
                description: "b2"
            %(port_3)d:
                native_vlan: 100
                description: "b3"
            %(port_4)d:
                native_vlan: 100
                description: "b4"
"""

    def test_untagged(self):
        first_host, second_host = self.net.hosts[0:2]
        # we expect to see the rewritten mac address.
        tcpdump_filter = ('icmp and ether dst 00:00:00:00:00:03')
        tcpdump_txt = self.tcpdump_helper(
            second_host, tcpdump_filter, [
                lambda: first_host.cmd(
                    'arp -s %s %s' % (second_host.IP(), '00:00:00:00:00:02')),
                lambda: first_host.cmd('ping -c1 %s' % second_host.IP())])
        self.assertTrue(re.search(
            '%s: ICMP echo request' % second_host.IP(), tcpdump_txt))

    def verify_dest_rewrite(self, source_host, overridden_host, rewrite_host, tcpdump_host):
        overridden_host.setMAC('00:00:00:00:00:02')
        rewrite_host.setMAC('00:00:00:00:00:03')
        rewrite_host.cmd('arp -s %s %s' % (overridden_host.IP(), overridden_host.MAC()))
        rewrite_host.cmd('ping -c1 %s' % overridden_host.IP())
        self.wait_until_matching_flow(
            {u'dl_dst': u'00:00:00:00:00:03'},
            table_id=6,
            actions=[u'OUTPUT:%u' % self.port_map['port_3']])
        tcpdump_filter = ('icmp and ether src %s and ether dst %s' % (
            source_host.MAC(), rewrite_host.MAC()))
        tcpdump_txt = self.tcpdump_helper(
            tcpdump_host, tcpdump_filter, [
                lambda: source_host.cmd(
                    'arp -s %s %s' % (rewrite_host.IP(), overridden_host.MAC())),
                # this will fail if no reply
                lambda: self.one_ipv4_ping(
                    source_host, rewrite_host.IP(), require_host_learned=False)])
        # ping from h1 to h2.mac should appear in third host, and not second host, as
        # the acl should rewrite the dst mac.
        self.assertFalse(re.search(
            '%s: ICMP echo request' % rewrite_host.IP(), tcpdump_txt))

    def test_switching(self):
        """Tests that a acl can rewrite the destination mac address,
           and the packet will only go out the port of the new mac.
           (Continues through faucet pipeline)
        """
        source_host, overridden_host, rewrite_host = self.net.hosts[0:3]
        self.verify_dest_rewrite(
            source_host, overridden_host, rewrite_host, overridden_host)

###################################################
class FaucetAuthenticationTest(FaucetTest):
    """Base class for the integration tests """

    RUN_GAUGE = False
    script_path = "/faucet-src/tests/dot1x_capflow_scripts" 
    pids = {}

    N_UNTAGGED = 5
    N_TAGGED = 0

    auth_server_port = 0

    def tearDown(self):
        if self.net is not None:
            host = self.net.hosts[0]
            print "about to kill everything"
            os.system('ps aux')
            for name, pid in self.pids.iteritems():
                print name, pid
                host.cmdPrint('kill ' + str(pid))

#            CLI(self.net)
            self.net.stop()

    def setup_host(self, hosts, switch):
        i = 0
        for host in hosts:
#        host = self.net.addHost(
#            "h{0}".format(i),
#                mac="00:00:00:00:00:1{0}".format(i),
#                privateDirs=['/etc/wpa_supplicant'])
#        self.net.addLink(host, switch)
            username = 'host11{0}user'.format(i)
            password = 'host11{0}pass'.format(i)
            i += 1
            host.cmdPrint("ls /etc/wpa_supplicant")

            wpa_conf = '''ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=0
eapol_version=2
ap_scan=0
network={
key_mgmt=IEEE8021X
eap=TTLS MD5
identity="%s"
anonymous_identity="%s"
password="%s"
phase1="auth=MD5"
phase2="auth=PAP password=password"
eapol_flags=0
}''' % (username, username, password)
            host.cmdPrint('''echo '{0}' > /etc/wpa_supplicant/{1}.conf'''.format(wpa_conf, host.name))
        


    def get_users(self):
        """
        Get the hosts that are users
        (ie not the portal or controller hosts)
        """
        users = []
        for host in self.net.hosts:
            if host.name.startswith("h"):
                users.append(host)
        return users

    def find_host(self, hostname):
        """Find a host when given the name"""
        for host in self.net.hosts:
            if host.name == hostname:
                return host
        return None

    def logon_capflow(self, host):
        """Log on a host using CapFlow"""
        cmd = "ip addr flush {0}-eth0 && dhclient {0}-eth0 -timeout 5".format(host.name)
        host.cmdPrint(cmd)
        host.cmdPrint("ip route add default via 10.0.12.1")
        host.cmdPrint('echo "nameserver 8.8.8.8" >> /etc/resolv.conf')
        cmd = 'lynx -cmd_script={0}_lynx'.format(
            os.path.join(self.script_path, host.name))
        host.cmdPrint(cmd)

    def logon_dot1x(self, host):
        """Log on a host using dot1x"""

        tcpdump_args = ' '.join((
            '-s 0',
            '-e',
            '-n',
            '-U',
            '-q',
            '-i %s-eth0' % host.name,
            '-w %s/%s-eth0.cap' % (self.tmpdir, host.name),
            '>/dev/null',
            '2>/dev/null',
        ))
        host.cmd('tcpdump %s &' % tcpdump_args)
        self.pids['%s-tcpdump' % host.name] = host.lastPid

        start_reload_count = self.get_configure_count()
        cmd = "wpa_supplicant -i{0}-eth0 -Dwired -c/etc/wpa_supplicant/{0}.conf &".format(host.name)
        print("cmd {}".format(cmd))
        time.sleep(10) # ?????
        print(host.cmdPrint(cmd))
        time.sleep(10)
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 60 {0}-eth0".format(host.name)
        print(host.cmdPrint(cmd))
        host.cmdPrint("ip route add default via 10.0.0.2")
        host.cmdPrint('echo "nameserver 8.8.8.8" >> /etc/resolv.conf')


        print('start_reload_count' + str(start_reload_count))
        end_reload_count = self.get_configure_count()
        print('end_reload_count' + str(end_reload_count))
        self.assertGreater(end_reload_count, start_reload_count)

    def fail_ping_ipv4(self, host, dst, retries=3):
        """Try to ping to a destination from a host. This should fail on all the retries"""
        self.require_host_learned(host)
        for _ in range(retries):
            ping_result = host.cmd('ping -c1 %s' % dst)
            print ping_result
            self.assertIsNone(re.search(self.ONE_GOOD_PING, ping_result), ping_result)

    def check_http_connection(self, host, retries=3):
        """Test the http connectivity"""
        for _ in range(retries):
            # pylint: disable=no-member 
            result = host.cmdPrint("wget --output-document=- --quiet 10.0.0.2:{}/index.txt".format(self.ws_port))
            print 'wgot'
            print result
            if re.search("This is a text file on a webserver",result) is not None:
                return True
        return False

    def run_controller(self, host):
        print 'Starting Controller ....'
#        host.cmdPrint('ryu-manager ryu.app.ofctl_rest faucet.faucet --wsapi-port 8084 &')
#        lastPid = host.lastPid
#        print lastPid
#        os.system('ps a')
#        host.cmdPrint('echo {} > {}/contr_pid'.format(lastPid, self.tmpdir))
#        os.system('ps a')

#        self.pids['faucet'] = lastPid

        # think want to get the auth.yaml, and change the location of the faucet.yaml to be the tmp dir.

        with open('/faucet-src/tests/config/auth.yaml', 'r') as f:
            httpconfig = f.read()

        host.cmdPrint('cp /faucet-src/b.py {}/'.format(self.tmpdir))
        host.cmdPrint('python3 {0}/b.py {0}/faucet.yaml {0} &'.format(self.tmpdir))
        self.pids['b'] = host.lastPid

        m = {}
        m['tmpdir'] = self.tmpdir
        m['promport'] = self.prom_port
        m['listenport'] = self.auth_server_port
        m['logger_location'] = self.tmpdir + '/httpserver.log'
        m['b'] = self.pids['b']


        host.cmdPrint('echo "%s" > %s/auth.yaml' % (httpconfig % m, self.tmpdir))
        host.cmdPrint('cp -r /faucet-src %s/' % self.tmpdir)
# > %s/httpserver.txt 2> %s/httpserver.err &'
        print host.cmdPrint('python3.5 %s/faucet-src/faucet/HTTPServer.py --config  %s/auth.yaml  > %s/httpserver.txt 2> %s/httpserver.err &'  % (self.tmpdir, self.tmpdir, self.tmpdir, self.tmpdir))
        print 'httpserver started'
        self.pids['auth_server'] = host.lastPid 
        print 'httpserver pid'
        print host.lastPid
        print host.cmdPrint('ip addr')

        tcpdump_args = ' '.join((
            '-s 0',
            '-e',
            '-n',
            '-U',
            '-q',
            '-i %s-eth0' % host.name,
            '-w %s/%s-eth0.cap' % (self.tmpdir, host.name),
            '>/dev/null',
            '2>/dev/null',
        ))
        host.cmd('tcpdump %s &' % tcpdump_args)



#        host.cmdPrint('tcpdump -i {0}-eth0 -vv >  {1}/controller-eth0.cap 2>&1 &'.format(host.name, self.tmpdir))
        self.pids['tcpdump'] = host.lastPid

        os.system('ps a')
        os.system('lsof -i tcp')
#        CLI(self.net)
        print 'Controller started.'


    def run_captive_portal(self, host):
        # TODO this was mostly copied from portal.sh so not sure if it actually works here.
        ipt = "# Generated by iptables-save v1.6.0 on Thu Feb 23 20:20:35 2017 \
*nat \
:PREROUTING ACCEPT [0:0] \
:INPUT ACCEPT [2:120] \
:OUTPUT ACCEPT [0:0] \
:POSTROUTING ACCEPT [0:0] \
-A PREROUTING -d 2.2.2.2/32 -i enp0s8 -p tcp -j REDIRECT \
-A PREROUTING -i enp0s8 -p tcp -j REDIRECT \
COMMIT \
# Completed on Thu Feb 23 20:20:35 2017 \
# Generated by iptables-save v1.6.0 on Thu Feb 23 20:20:35 2017 \
*filter \
:INPUT ACCEPT [111042:871714493] \
:FORWARD ACCEPT [9:500] \
:OUTPUT ACCEPT [79360:937635748] \
COMMIT \
# Completed on Thu Feb 23 20:20:35 2017 \
"
        host.cmdPrint('#echo {0}  | iptables-restore' \
                      '#cd /home/$(whoami)/sdn-authenticator-webserver/' \
                      '#nohup java -cp uber-captive-portal-webserver-1.0-SNAPSHOT.jar Main config.yaml > /home/$(whoami)/portal_webserver.out 2>&1 &' \
                      '#echo $! > /home/$(whoami)/portal_webserver_pid.txt')
        self.pids['captive_portal'] = host.lastPid

    def run_hostapd(self, host):
#        host.cmdPrint('cp')
        # pylint: disable=no-member
        contr_num = self.net.controller.name.split('-')[1]

        print 'Starting hostapd ....'
        host.cmdPrint('''echo "interface={0}-eth0\n
driver=wired\n
logger_stdout=-1\n
logger_stdout_level=0\n
ieee8021x=1\n
eap_reauth_period=3600\n
use_pae_group_addr=0\n
eap_server=1\n
eap_user_file=/root/hostapd-d1xf/hostapd/hostapd.eap_user\n" > {1}/{0}-wired.conf'''.format(host.name , self.tmpdir))

        host.cmdPrint('cp -r /root/hostapd-d1xf/ {}/hostapd-d1xf'.format(self.tmpdir))


#cd /root/hostapd-d1xf/hostapd && \
        print host.cmdPrint('''sed -ie  's/10\.0\.0\.2/192\.168\.{0}\.3/g' {1}/hostapd-d1xf/src/eap_server/eap_server.c && \
sed -ie  's/10\.0\.0\.2/192\.168\.{0}\.3/g' {1}/hostapd-d1xf/src/eapol_auth/eapol_auth_sm.c && \
sed -ie 's/8080/{2}/g' {1}/hostapd-d1xf/src/eap_server/eap_server.c && \
sed -ie 's/8080/{2}/g' {1}/hostapd-d1xf/src/eapol_auth/eapol_auth_sm.c && \
cd {1}/hostapd-d1xf/hostapd && \
make'''.format(contr_num, self.tmpdir, self.auth_server_port))

        print 'made hostapd'
#        host.cmdPrint("""sed -i 's/172\.30\.15\.3/172\.30\.13\.3/g' %s/hostapd""" % (self.tmpdir))
#        host.cmdPrint("""sed -i 's/172\.30\.13\.3/172\.30\.%s\.3/g' %s/hostapd""" % (contr_num, self.tmpdir))
#        host.cmdPrint("""sed -i 's/qwert/{0}/g' {1}/hostapd""".format(self.auth_server_port, self.tmpdir))

        host.cmdPrint('{0}/hostapd-d1xf/hostapd/hostapd -d {0}/{1}-wired.conf > {0}/hostapd.out 2>&1 &'.format(self.tmpdir, host.name))
        self.pids['hostapd'] = host.lastPid

        tcpdump_args = ' '.join((
            '-s 0',
            '-e',
            '-n',
            '-U',
            '-q',
            '-i %s-eth1' % host.name,
            '-w %s/%s-eth1.cap' % (self.tmpdir, host.name),
            '>/dev/null',
            '2>/dev/null',
        ))
        host.cmd('tcpdump %s &' % tcpdump_args)
        self.pids['p1-tcpdump'] = host.lastPid

        tcpdump_args = ' '.join((
            '-s 0',
            '-e',
            '-n',
            '-U',
            '-q',
            '-i %s-eth0' % host.name,
            '-w %s/%s-eth0.cap' % (self.tmpdir, host.name),
            '>/dev/null',
            '2>/dev/null',
        ))
        host.cmd('tcpdump %s &' % tcpdump_args)
        self.pids['p0-tcpdump'] = host.lastPid

        print os.system('ps aux') 

    def makeDHCPconfig(self, filename, intf, gw, dns ):

        DNSTemplate = """
start       10.0.12.10
end     10.0.12.255
option  subnet  255.0.0.0
option  domain  local
option  lease   120  # seconds
"""

        "Create a DHCP configuration file"
        config = (
            'interface %s' % intf,
            DNSTemplate,
            'option router %s' % gw,
            'option dns %s' % dns,
            '' )
        with open( filename, 'w' ) as f:
            f.write( '\n'.join( config ) )

    def startDHCPserver(self, host, gw, dns ):
        "Start DHCP server on host with specified DNS server"
        print( '* Starting DHCP server on', host, 'at', host.IP(), '\n' )
        dhcpConfig = '/tmp/%s-udhcpd.conf' % host
        self.makeDHCPconfig( dhcpConfig, host.defaultIntf(), gw, dns )
        host.cmd( 'udhcpd -f', dhcpConfig,
          '1>/tmp/%s-dhcp.log 2>&1  &' % host )

    def setup(self):
        super(FaucetAuthenticationTest, self).setUp()



class FaucetAuthenticationSingleSwitchTest(FaucetAuthenticationTest):
    ws_port = 0
    clients = []
    CONFIG_GLOBAL = """
vlans:
    100:
        description: "untagged"
acls:
    port_faucet-1_3:
        - rule:
            _name_: d1x
            actions:
                allow: 1
                dl_dst: 70:6f:72:74:61:6c
            dl_type: 34958
        - rule:
            _name_: redir41x
            actions:
                allow: 1
                output:
                    dl_dst: 70:6f:72:74:61:6c
    port_faucet-1_4:
        - rule:
            _name_: d1x
            actions:
                allow: 1
                dl_dst: 70:6f:72:74:61:6c
            dl_type: 34958
        - rule:
            _name_: redir41x
            actions:
                allow: 1
                output:
                    dl_dst: 70:6f:72:74:61:6c

    port_faucet-1_5:
        - rule:
            _name_: d1x
            actions:
                allow: 1
                dl_dst: 70:6f:72:74:61:6c
            dl_type: 34958
        - rule:
            _name_: redir41x
            actions:
                allow: 1
                output:
                    dl_dst: 70:6f:72:74:61:6c
"""

    CONFIG = """
        interfaces:
            %(port_1)d:
                name: portal
                native_vlan: 100
            %(port_2)d:
                name: gateway
                native_vlan: 100
            %(port_3)d:
                name: host1
                native_vlan: 100
                acl_in: port_faucet-1_%(port_3)d
                auth_mode: access
            %(port_4)d:
                name: host2
                native_vlan: 100
                acl_in: port_faucet-1_%(port_4)d
                auth_mode: access
            %(port_5)d:
                name: host3
                native_vlan: 100
                acl_in: port_faucet-1_%(port_5)d
                auth_mode: access
"""
    def setUp(self):
        super(FaucetAuthenticationSingleSwitchTest, self).setUp()
        self.topo = self.topo_class(
            self.ports_sock, dpid=self.dpid, n_tagged=0, n_untagged=5)

        print 'sINGLE sWITCH test'
#        self.net = None
#        self.dpid = "1"

#        self.v2_config_hashes, v2_dps = dp_parser('/faucet-src/tests/config/testconfigv2-1x.yaml', 'test_auth')
#        self.v2_dps_by_id = {}
#        for dp in v2_dps:
#            self.v2_dps_by_id[dp.dp_id] = dp
#        self.v2_dp = self.v2_dps_by_id[0x1]

        # copy config file from tests/config to /etc/ryu/faucet/facuet/yaml
#        try:
#            os.makedirs('/etc/ryu/faucet')
#        except:
#            pass
#        shutil.copyfile("/faucet-src/tests/config/testconfigv2-1x-1s.yaml", "/etc/ryu/faucet/faucet.yaml")
        print 'finding free port'
        port = 0
        while port <=9999:
            port, _ = faucet_mininet_test_util.find_free_port(
                self.ports_sock, self._test_name())
            print 'auth_server_port: ' + str(port)

        self.auth_server_port = port
        self.start_net()
        self.start_programs() 

    def start_programs(self):
        """Start Mininet."""
#        self.net = Mininet(build=False)
#        c0 = self.net.addController(
#            "c0",
#            controller=FaucetDot1xCapFlowController,
#            ip='127.0.0.1',
#            port=6653,
#            switch=OVSSwitch)

        print 'Controller'
        print self.net.controller

 
#        switch1 = self.net.addSwitch(
#            "s1", cls=OVSSwitch, inband=True, protocols=["OpenFlow13"])
#        switch1.start([c0])
        # pylint: disable=unbalanced-tuple-unpacking
        portal, interweb, h0, h1, h2 = self.net.hosts
        # pylint: disable=no-member
        lastPid = self.net.controller.lastPid
        print lastPid
#        os.system('ps a')
        # pylint: disable=no-member
        self.net.controller.cmdPrint('echo {} > {}/contr_pid'.format(lastPid, self.tmpdir))
        self.pids['faucet'] = lastPid

#            self.net.addHost(
#            "portal", ip='10.0.12.3/24', mac="70:6f:72:74:61:6c")
#        self.net.addLink(portal, switch1)
        # pylint: disable=no-member
        contr_num = self.net.controller.name.split('-')[1]

        self.net.addLink(
            portal,
            self.net.controller,
#            params1={'ip': '172.30.13.2/24'},
#            params2={'ip': '172.30.13.3/24'})
#        print 'portal ping controller'
#        print portal.cmdPrint('ping -c5 172.30.13.2')
            params1={'ip': '192.168.%s.2/24' % contr_num},
            params2={'ip': '192.168.%s.3/24' % contr_num})
        print 'portal ping controller'
        print portal.cmdPrint('ping -c5 192.168.%s.3' % contr_num)
        self.run_controller(self.net.controller)

#        interweb = self.net.addHost(
#            "interweb", ip='10.0.12.1/24', mac="08:00:27:ee:ee:ee")
#        self.net.addLink(interweb, switch1)

        interweb.cmdPrint('echo "This is a text file on a webserver" > index.txt')
        self.ws_port, _ = faucet_mininet_test_util.find_free_port(
            self.ports_sock, self._test_name())
        print "ws_port"
        print self.ws_port        
        interweb.cmdPrint('python -m SimpleHTTPServer {0} &'.format(self.ws_port))

 #       for i in range(0, 3):
        hosts = self.net.hosts[2:]

        print 'hosts'
        print self.net.hosts
        print 'clients'
        print hosts
        self.clients = hosts
        self.setup_host(hosts, self.net.switch)
                        

#        self.net.build()
#        self.net.start()
        self.startDHCPserver(interweb, gw='10.0.0.2', dns='8.8.8.8')

        self.run_hostapd(portal)
        portal.cmdPrint('ip route add 10.0.0.0/8 dev {}-eth0'.format(portal.name))


class FaucetAuthenticationSomeLoggedOnTest(FaucetAuthenticationSingleSwitchTest):
    """Check if authenticated and unauthenticated users can communicate"""

    def ping_between_hosts(self, users):
        """Ping between the specified hosts"""
        for user in users:
            user.defaultIntf().updateIP()

        #ping between the authenticated hosts
        ploss = self.net.ping(hosts=users[:2], timeout='5')
        self.assertAlmostEqual(ploss, 0)

        #ping between an authenticated host and an unauthenticated host
        ploss = self.net.ping(hosts=users[1:], timeout='5')
        self.assertAlmostEqual(ploss, 100)
        ploss = self.net.ping(hosts=[users[0], users[2]], timeout='5')
        self.assertAlmostEqual(ploss, 100)

    def QWERTYtest_onlycapflow(self):
        """Only authenticate through CapFlow """
        users = self.get_users()
        self.logon_capflow(users[0])
        self.logon_capflow(users[1])
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
            users[2].name)
        users[2].cmdPrint(cmd)
        self.ping_between_hosts(users)

    def test_onlydot1x(self):
        """Only authenticate through dot1x"""
        users = self.clients
        self.logon_dot1x(users[0])
        self.logon_dot1x(users[1])
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
            users[2].name)
        users[2].cmdPrint(cmd)
        self.ping_between_hosts(users)

    def QWERTYtest_bothauthentication(self):
        """Authenicate one user with dot1x and the other with CapFlow"""
        users = self.get_users()
        self.logon_dot1x(users[0])
        self.logon_capflow(users[1])
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
            users[2].name)
        users[2].cmdPrint(cmd)
        self.ping_between_hosts(users)


class FaucetAuthenticationNoLogOnTest(FaucetAuthenticationSingleSwitchTest):
    """Check the connectivity when the hosts are not authenticated"""

    def test_nologon(self):
        """
        Get the users to ping each other 
        before anyone has authenticated
        """
        users = self.clients
        for user in users:
            cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
                user.name)
            user.cmdPrint(cmd)
            user.defaultIntf().updateIP()

        ploss = self.net.ping(hosts=users, timeout='5')
        self.assertAlmostEqual(ploss, 100)


class FaucetAuthenticationDot1XLogonTest(FaucetAuthenticationSingleSwitchTest):
    """Check if a user can logon successfully using dot1x"""

    def test_dot1xlogon(self):
        """Log on using dot1x"""
#        os.system("ps a")
        h0 = self.clients[0]
        interweb = self.net.hosts[1]
        self.logon_dot1x(h0) 
        self.one_ipv4_ping(h0, '10.0.0.2')
        result = self.check_http_connection(h0)
        self.assertTrue(result)


class FaucetAuthenticationDot1XLogoffTest(FaucetAuthenticationSingleSwitchTest):
    """Log on using dot1x and log off"""

    def test_logoff(self):
        """Check that the user cannot go on the internet after logoff"""
        h0 = self.clients[0]
        interweb = self.net.hosts[1]
        self.logon_dot1x(h0)
#        time.sleep(5)
        self.one_ipv4_ping(h0, '10.0.0.2')
#        time.sleep(5)
        result = self.check_http_connection(h0)

        self.assertTrue(result)
        print 'wpa_cli status'
        print h0.cmdPrint('wpa_cli status')
        print h0.cmdPrint("wpa_cli logoff")
        time.sleep(60)
        print 'wpa_cli status'
        print h0.cmdPrint('wpa_cli status')
        self.fail_ping_ipv4(h0, '10.0.0.2')
        result = self.check_http_connection(h0)
        self.assertFalse(result)
