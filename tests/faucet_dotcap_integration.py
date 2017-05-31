#!/usr/bin/python
"""
Classes for testing the FAUCET and Authentication
methods (Dot1x and Capflow)
"""
import unittest
import time
import os
import sys
import re
import inspect
import shutil
import tempfile
import threading

from mininet.net import Mininet
from mininet.link import Intf
from mininet.node import OVSSwitch
from mininet.node import RemoteController
from mininet.cli import CLI

import faucet_mininet_test_base
from faucet_mininet_test import make_suite
from faucet_mininet_test import parse_args
import faucet_mininet_test_util

#from config_parser import dp_parser


class FaucetDot1xCapFlowController(RemoteController):
    """A controller that uses the integrated version """

    def __init__(self, name, **kwargs):
        self.ofctl_port = 8084
        RemoteController.__init__(self, name, **kwargs)
    
    def checkListening(self):
        "Overridden to do nothing."
        return


class FaucetAuthenticationTest(faucet_mininet_test_base.FaucetTestBase):
    """Base class for the integration tests """

    RUN_GAUGE = False
    script_path = "/faucet-src/tests/dot1x_capflow_scripts" 
    pids = {}

    def tearDown(self):
        if self.net is not None:
            host = self.net.hosts[0]
            print "about to kill everything"

            for name, pid in self.pids.iteritems():
                print name, pid
                host.cmdPrint('kill ' + str(pid))


            self.net.stop()

    def setup_host(self, i, switch):
        host = self.net.addHost(
            "h{0}".format(i),
                mac="00:00:00:00:00:1{0}".format(i),
                privateDirs=['/etc/wpa_supplicant'])
        self.net.addLink(host, switch)
        username = 'host11{0}user'.format(i)
        password = 'host11{0}pass'.format(i)
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
        cmd = "wpa_supplicant -i{0}-eth0 -Dwired -c/etc/wpa_supplicant/{0}.conf &".format(host.name)
        print("cmd {}".format(cmd))
        print(host.cmdPrint(cmd))
        time.sleep(2)
        cmd = "ip addr flush {0}-eth0 && dhcpcd {0}-eth0".format(host.name)
        print(host.cmdPrint(cmd))
        host.cmdPrint("ip route add default via 10.0.12.1")
        host.cmdPrint('echo "nameserver 8.8.8.8" >> /etc/resolv.conf')

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
            result = host.cmdPrint("wget --output-document=- --quiet 10.0.12.1:8080/index.txt")
            print result
            if re.search("This is a text file on a webserver",result) is not None:
                return True
        return False

    def run_controller(self, host):
        print 'Starting Controller ....'
        host.cmdPrint('ryu-manager ryu.app.ofctl_rest faucet.faucet --wsapi-port 8084 &')
        lastPid = host.lastPid
        host.cmdPrint('echo {} > /etc/ryu/faucet/contr_pid'.format(lastPid))
        self.pids['faucet'] = lastPid

        host.cmdPrint('python3.5 /faucet-src/faucet/HTTPServer.py --config  /faucet-src/tests/config/auth.yaml > httpserver.txt &')
        self.pids['auth_server'] = host.lastPid 
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
        print 'Starting hostapd ....'
        host.cmdPrint('cd /root/;' \
                            '/root/hostapd-d1xf/hostapd/hostapd -d /root/hostapd-d1xf/hostapd/wired.conf > hostapd.out 2>&1 &')
        self.pids['hostapd'] = host.lastPid

    def makeDHCPconfig(self, filename, intf, gw, dns ):

        DNSTemplate = """
start       10.0.12.10
end     10.0.12.20
option  subnet  255.255.255.0
option  domain  local
option  lease   60  # seconds
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

class FaucetAuthenticationMultiSwitchTest(FaucetAuthenticationTest):

    def setUp(self):

        print 'mULTIsWITCH test'

        self.net = None
        self.dpid = "1"

#        self.v2_config_hashes, v2_dps = dp_parser('/faucet-src/tests/config/testconfigv2-1x.yaml', 'test_auth')
#        self.v2_dps_by_id = {}
#        for dp in v2_dps:
#            self.v2_dps_by_id[dp.dp_id] = dp
#        self.v2_dp = self.v2_dps_by_id[0x1]

        # copy config file from tests/config to /etc/ryu/faucet/facuet/yaml
        try:
            os.makedirs('/etc/ryu/faucet')
        except:
            pass
        shutil.copyfile("/faucet-src/tests/config/testconfigv2-1x.yaml", "/etc/ryu/faucet/faucet.yaml")

        self.start_net()


    def start_net(self):
        """Start Mininet."""
        os.system('pwd')
        os.system('ls scripts')
        self.net = Mininet(build=False)
        c0 = self.net.addController(
            "c0",
            controller=FaucetDot1xCapFlowController,
            ip='127.0.0.1',
            port=6653,
            switch=OVSSwitch)
        self.contr = c0
        self.run_controller(c0)
 
        switch1 = self.net.addSwitch(
            "s1", cls=OVSSwitch, inband=True, protocols=["OpenFlow13"])
        switch1.start([c0])

        switch2 = self.net.addSwitch(
            "s2", cls=OVSSwitch, inband=False, protocols=["OpenFlow13"])
        self.net.addLink(switch1, switch2)

        portal = self.net.addHost(
            "portal", ip='10.0.12.3/24', mac="70:6f:72:74:61:6c")
        self.net.addLink(portal, switch1)
        self.net.addLink(
            portal,
            c0,
            params1={'ip': '10.0.13.2/24'},
            params2={'ip': '10.0.13.3/24'})

        interweb = self.net.addHost(
            "interweb", ip='10.0.12.1/24', mac="08:00:27:ee:ee:ee")
        self.net.addLink(interweb, switch1)

        interweb.cmdPrint('echo "This is a text file on a webserver" > index.txt')
        interweb.cmdPrint('python -m SimpleHTTPServer 8080 &')

        for i in range(0, 3):
            self.setup_host(i, switch2)
                        
        self.net.build()
        for iface in ['eth0', ]:
            # Connect the switch to the eth0 interface of this host machine
            Intf(iface, node=switch1)

        self.net.start()
        self.startDHCPserver(interweb, gw='10.0.12.1', dns='8.8.8.8')
        print "start portal"
        self.run_hostapd(portal)
        portal.cmdPrint('ip route add 10.0.0.0/8 dev portal-eth0')
        os.system("ps aux")


class FaucetAuthenticationSingleSwitchTest(FaucetAuthenticationTest):

    def setUp(self):

        print 'sINGLE sWITCH test'
        self.net = None
        self.dpid = "1"

#        self.v2_config_hashes, v2_dps = dp_parser('/faucet-src/tests/config/testconfigv2-1x.yaml', 'test_auth')
#        self.v2_dps_by_id = {}
#        for dp in v2_dps:
#            self.v2_dps_by_id[dp.dp_id] = dp
#        self.v2_dp = self.v2_dps_by_id[0x1]

        # copy config file from tests/config to /etc/ryu/faucet/facuet/yaml
        try:
            os.makedirs('/etc/ryu/faucet')
        except:
            pass
        shutil.copyfile("/faucet-src/tests/config/testconfigv2-1x-1s.yaml", "/etc/ryu/faucet/faucet.yaml")

        self.start_net()


    def start_net(self):
        """Start Mininet."""
        self.net = Mininet(build=False)
        c0 = self.net.addController(
            "c0",
            controller=FaucetDot1xCapFlowController,
            ip='127.0.0.1',
            port=6653,
            switch=OVSSwitch)
        self.contr = c0
        self.run_controller(c0)
 
        switch1 = self.net.addSwitch(
            "s1", cls=OVSSwitch, inband=True, protocols=["OpenFlow13"])
        switch1.start([c0])

        portal = self.net.addHost(
            "portal", ip='10.0.12.3/24', mac="70:6f:72:74:61:6c")
        self.net.addLink(portal, switch1)
        self.net.addLink(
            portal,
            c0,
            params1={'ip': '10.0.13.2/24'},
            params2={'ip': '10.0.13.3/24'})

        interweb = self.net.addHost(
            "interweb", ip='10.0.12.1/24', mac="08:00:27:ee:ee:ee")
        self.net.addLink(interweb, switch1)

        interweb.cmdPrint('echo "This is a text file on a webserver" > index.txt')
        interweb.cmdPrint('python -m SimpleHTTPServer 8080 &')

        for i in range(0, 3):
            self.setup_host(i, switch1)
                        

        self.net.build()
        self.net.start()
        self.startDHCPserver(interweb, gw='10.0.12.1', dns='8.8.8.8')

        self.run_hostapd(portal)
        portal.cmdPrint('ip route add 10.0.0.0/8 dev portal-eth0')



class FaucetAuthenticationCapFlowLogonTest(FaucetAuthenticationMultiSwitchTest):
    """Check if a user can logon successfully using CapFlow"""

    def test_capflowlogon(self):
        """Log on using CapFlow"""
        h0 = self.find_host("h0")
        self.logon_capflow(h0)
        self.one_ipv4_ping(h0, "www.google.co.nz")
        result = self.check_http_connection(h0)
        self.assertTrue(result)


class FaucetAuthenticationSomeLoggedOnTest(FaucetAuthenticationMultiSwitchTest):
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

    def test_onlycapflow(self):
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
        users = self.get_users()
        self.logon_dot1x(users[0])
        self.logon_dot1x(users[1])
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
            users[2].name)
        users[2].cmdPrint(cmd)
        self.ping_between_hosts(users)

    def test_bothauthentication(self):
        """Authenicate one user with dot1x and the other with CapFlow"""
        users = self.get_users()
        self.logon_dot1x(users[0])
        self.logon_capflow(users[1])
        cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
            users[2].name)
        users[2].cmdPrint(cmd)
        self.ping_between_hosts(users)


class FaucetAuthenticationNoLogOnTest(FaucetAuthenticationMultiSwitchTest):
    """Check the connectivity when the hosts are not authenticated"""

    def test_nologon(self):
        """
        Get the users to ping each other 
        before anyone has authenticated
        """
        users = self.get_users()
        for user in users:
            cmd = "ip addr flush {0}-eth0 && dhcpcd --timeout 5 {0}-eth0".format(
                user.name)
            user.cmdPrint(cmd)
            user.defaultIntf().updateIP()

        ploss = self.net.ping(hosts=users, timeout='5')
        self.assertAlmostEqual(ploss, 100)


class FaucetAuthenticationDot1XLogonTest(FaucetAuthenticationMultiSwitchTest):
    """Check if a user can logon successfully using dot1x"""

    def test_dot1xlogon(self):
        """Log on using dot1x"""
        os.system("ps aux")
        h0 = self.find_host("h0")
        self.logon_dot1x(h0) 
        self.one_ipv4_ping(h0, "10.0.12.1")
        result = self.check_http_connection(h0)
        self.assertTrue(result)


class FaucetAuthenticationDot1XLogoffTest(FaucetAuthenticationMultiSwitchTest):
    """Log on using dot1x and log off"""

    def test_logoff(self):
        """Check that the user cannot go on the internet after logoff"""
        h0 = self.find_host("h0")
        self.logon_dot1x(h0)
        self.one_ipv4_ping(h0, "10.0.12.1")
        time.sleep(5)
        result = self.check_http_connection(h0)

        self.assertTrue(result)
        print h0.cmdPrint("wpa_cli logoff")
        time.sleep(10)
        self.fail_ping_ipv4(h0, "10.0.12.1")
        result = self.check_http_connection(h0)
        self.assertFalse(result)


class FaucetAuthenticationCapFlowLogoffTest(FaucetAuthenticationMultiSwitchTest):
    """Log on using CapFlow and log off"""

    def test_logoff(self):
        """Check that the user cannot go on the internet after logoff"""
        h0 = self.find_host("h0")
        self.logon_capflow(h0)
        self.one_ipv4_ping(h0, "www.google.co.nz")
        result = self.check_http_connection(h0)
        self.assertTrue(result)
        h0.cmdPrint("timeout 10s curl http://10.0.12.3/loggedout")
        self.fail_ping_ipv4(h0, "www.google.co.nz")
        result = self.check_http_connection(h0)
        self.assertFalse(result)

def start_all_tests():
    """Start all the tests in this file"""
    requested_test_classes, clean, keep_logs, nocheck, serial, excluded_test_classes = parse_args()
    tests = unittest.TestSuite()
    root_tmpdir = tempfile.mkdtemp(prefix='faucet-tests-')
    ports_sock = os.path.join(root_tmpdir, 'ports-server')
    ports_server = threading.Thread(
        target=faucet_mininet_test_util.serve_ports, args=(ports_sock,))
    ports_server.setDaemon(True)
    ports_server.start()
    config = None
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if requested_test_classes and name not in requested_test_classes:
            continue

        if inspect.isclass(obj) and name.startswith("FaucetAuthentication"):
            tests.addTest(make_suite(obj, config, root_tmpdir, ports_sock))

            silent_obj = type(obj.__name__ + 'Single', obj.__bases__, dict(obj.__dict__))
            silent_obj.__bases__ = (FaucetAuthenticationSingleSwitchTest,)
            tests.addTest(make_suite(silent_obj, config, root_tmpdir, ports_sock))
    unittest.TextTestRunner(verbosity=2).run(tests)


if __name__ == '__main__':
    start_all_tests()