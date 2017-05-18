# 801.1X & Captive Portal Authentication with Faucet

This release is a work in progress, and there are bugs.

If you notice something odd, or have any suggestions please create a Github issue or email michael.baird@ecs.vuw.ac.nz

| Table of Contents |
| ------------------- |
| [Introduction](#introduction) |
| [Features](#features) |
| [Limitations](#limitations) |
| [802.1X](#8021x) |
| [802.1X Components](#components) |
| [802.1X Overview](#overview) |
| [802.1X Setup](#setup) |
| [802.1X Running](#running) |
| [Captive Portal](#captive-portal) |
| [TODO](#todo) |


# Introduction

This system is made up of 5 general components as shown in the diagram below: Hosts (end users), authentication server(s), the Internet, OpenFlow Controller, and an OpenFlow 1.3 capable switch.

The **Hosts** must either support 802.1X authentication or have a web browser/be able to make HTTP requests.
This has been tested with Ubuntu 16.04 (with [wpa_supplicant](https://w1.fi/wpa_supplicant/) providing 802.1X support).

The **Authentication server(s)** are Network Function Virtualisation (NFV) style servers.
[Hostapd](https://w1.fi/hostapd/) provides the 802.1X authentication, and a captive portal is provided by [sdn-authenticator-webserver](https://github.com/bairdo/sdn-authenticator-webserver).

The **Internet** provides access to the Internet and at this stage DHCP and DNS servers (which are used by the captive portal).

The **Controller** is the [Ryu](osrg.github.io/ryu) OpenFlow Controller, [Faucet](https://github.com/reannz/faucet), and a HTTP 'server' for configuring Faucet across the network.

The **OpenFlow Switch** is an OpenFlow 1.3 switch we currently use [OpenVSwitch](openvswitch.org).
In the future we hope to run on [Allied Telesis ATx930](https:/www.alliedtelesis.com/products/x930-series).

The diagram below is an example of what we have tested with, in the future we hope to verify different configurations such as single switch, and multiple switch with multiple Authentication servers at different switches.
Take note of the link between the Authentication Server and the OpenFlow Controller.
This allows the authentication traffic to avoid the dataplane of the switch and therefore any end-user traffic, and allow the Controller to run in out-of-band mode.

```
+-----------+        +--------------+                    +-----------+
|           |        |              |                    |           |
|           |        |Authentication|                    | OpenFlow  |
|  Internet |        |    Server    +--------------------+Controller |
|           |        |              |                    |           |
|           |        |              |                    |           |
|           |        |              |                    |           |
+-----+-----+        +------+-------+                    +-----+-----+
      |                     |                                  |
      |                     |                                  |
      |                     |                                  |
      |                     |                                  |
+-----+---------------------+----------------------------------+-----+
|                                                                    |
|                                                                    |
|                        OpenFlow Swtich                             |
|                                                                    |
|                                                                    |
+--------------------------------+-----------------------------------+
                                 |
                                 |
                                 |
                                 |
+--------------------------------+-----------------------------------+
|                                                                    |
|                                                                    |
|                           OpenFlow Switch                          |
|                                                                    |
|                                                                    |
+----+--------------+------------+--------------------------+--------+
     |              |            |                          |
+----+---+      +---+---+     +--+---+                   +--+---+
|        |      |       |     |      |                   |      |
|  Host  |      | Host  |     | Host |        ...        | Host |
|        |      |       |     |      |                   |      |
+--------+      +-------+     +------+                   +------+
```

## 'Features'
- 802.1X in SDN environment.
- Captive Portal Fallback when host unresponsive to attempts to authenticate via 802.1X.
- Fine grained access control, assign ACL rules that match any 5 tuple (Ethernet src/dst, IP src/dst & transport src/dst port) or any Ryu match field for that matter, not just putting user on a VLAN.
- Authentication Servers can communicate with a RADIUS Server (FreeRADIUS, Cisco ISE, ...).
- Support faucet.yaml 'include' option (see limitations below).


## Limitations
- .yaml configuration files must have 'dps' & 'acls' as top level (no indentation) objects, and only declared once across all files.
- Weird things may happen if a user moves 'access' port, they should successfully reauthenticate, however they might have issues if a malicious user fakes the authenticated users MAC on the old port (poisoning the MAC-port learning table), and if they (malicious user) were to log off the behaviour is currently 'undefined'.
See [TODO](#todo) for more.

- Currently the authenticated rules are hardcoded into HTTPServer.py as block traffic to 8.8.8.8, and allow everything else from the authenticated MAC address.
- Captive Portal transmits passwords in cleartext between user and webserver, need to add HTTPS support.

## 802.1X

### Components
- Hostapd
- RADIUS Server (Optional, can use the hostapd integrated one)
- Faucet
- HTTPServer

### Overview
A user can be in two states authenticated and unauthenticated.
When a user is unauthenticated (default state) all of their traffic is redirected to the hostapd server via a destination MAC address rewrite.
This allows the hostapd process to inform the client that the network is using 802.1X with a EAP-Request message.
When a user sends the EAP-Logoff message  they are unauthenticated from the port.

When a user successfully authenticates Access Control List (ACL) rules get applied.
These ACLs can match on any field that Ryu supports (and therefore Faucet), see [Ryu documentation](http://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html#flow-match-structure).
Typically these 'authorisation' rules should include the 'dl_src' with the users MAC address (TODO allow runtime insertion of MAC from rule file) to ensure that the rule gets applied to the user, however if desired this is not necessary, **BUT this could mean that unauthenticated users can use the network!** so do so at your own risk.

The hostapd process typically runs on its own server and has a separate (from the switch's dataplane) network connection to the controller.
This connection is used for HTTP messages to the HTTPServer process when the state of a user changes.

If desired the RADIUS server can be directly connected to the switch (with appropriate ACLs) or through a 'private' network to the hostapd server.

Once the captive portal is working reliably the hostapd server will be able to assist in providing a 'fallback' to the captive portal for clients who do not want to use 802.1X.
 
### Setup
#### Authentication Server
##### Hostapd
- Get hostapd. Note not official hostapd. This contains modifications to communicate with our Controller HTTPServer.

```bash
$ git clone https://github.com/bairdo/hostapd-d1xf.git
```

- Configure the build.
The provided .config should suffix. However if you wish to modify it, we basically need the wired driver, and you may also want the integrated RADIUS Server.
- Build.
```bash
make && sudo make install
```

- hostapd/wired.conf provides the configuration file for hostapd.

The Following are required (the acct_* may not be required and at this time hostapd will not provide any meaningful accounting statistics to your RADIUS server):
```ini
interface=<interface to listen on>
driver=wired
ieee8021x=1
use_pae_group_addr=0
auth_server_addr=<RADIUS SERVER IP>
auth_server_port=<RADIUS SERVER PORT>
auth_server_shared_secret=<RADIUS SERVER SECRET>

acct_server_addr=<ACCOUNTING RADIUS SERVER IP>
acct_server_port=<ACCOUNTING RADIUS SERVER PORT>
acct_server_shared_secret=<ACCOUNTING RADIUS SERVER SECRET>
```

##### RADIUS Server
- Follow the setup and installation instructions for the RADIUS server of your choice.

- Hostap will authenticate users using the 802.1X methods specified by the RADIUS Server.
If you are using Windows clients EAP-MSCHAPv2 will need to be enabled.

- We (the developer) used FreeRadius and the hostap integrated RADIUS server during development, and Cisco ISE during deployment.

#### Controller
##### Faucet
- Get faucet. Note: NOT the official reannz repo at this time
```bash
$ git clone https://github.com/bairdo/faucet.git
```

We recommend starting off with the following configuration:

###### faucet.yaml
```yaml
version: 2
vlans:
      100:
            name: vlan100

dps:
      ovs-switch:
            dp_id: 1
            hardware: Open vSwitch
            interfaces:
                  1:
                        name: portal
                        native_vlan: 100
                        acl_in: allow_all_acl
                  2:
                        name: gateway
                        native_vlan: 100
                        acl_in: allow_all_acl
                  4:
                        name: hosts
                        native_vlan: 100
                        acl_in: allow_all_acl

      ovs-hosts-switch:
            dp_id: 2
            hardware: Open vSwitch
            interfaces:
                  1:
                        name: h1
                        native_vlan: 100
                        acl_in: port2_1
                        mode: access
                  2:
                        name: h2
                        native_vlan: 100
                        acl_in: port2_2
                        mode: access
                  3:
                        name: switch1
                        native_vlan: 100
                        acl_in: allow_all_acl

include:
    - acls.yaml
```

###### acls.yaml
```yaml
acls:
      allow_all_acl:
          - rule:
                  actions:
                        allow: 1
      port2_1:
          - rule:
                  # This rule must be at the top of the port acl.
                  # It will redirect all 802.1X traffic to the hostap server that
                  #  is running on mac address 08:00:27:00:03:02.
                  name: d1x
                  dl_type: 34958
                  actions:
                        allow: 1
          # Once a user has authenticated their rules will be inserted here, below the d1x rule,
          #  with the most recent being nearer the top, and therefore they will have a higher priority on the switch.
          - rule:
                  # This rule should be near the bottom.
                  # It will redirect all traffic to the hostap server that is
                  #  running on mac address 08:00:27:00:03:02.
                  # Used for getting hostap to send EAPOL-request messages, to notify the client to start 802.1X.
                  name: redir41x
                  actions:
                        allow: 1
                        dl_dst: 08:00:27:00:03:02
      port2_2:
          - rule:
                  name: d1x
                  dl_type: 34958
                  actions:
                        allow: 1
          - rule:
                  name: redir41x
                  actions:
                        allow: 1
                        dl_dst: 08:00:27:00:03:02
```
These configuration files are based on the network diagram at the top.

- Each 'interface' that is to use 802.1X authentication requires two configurations:

1. The key 'mode' must be set with the value 'access'

2. Each 'acl_in' must be unique to each interface ('port acl' from here on). This will restrict a user to the interface that they authenticate on. (It may be possible to have users authenticated across multiple interfaces (if they only authenticate on one), if the acl spans multiple ports, but this is not tested.)

- 'port2_1' & 'port2_2' show the rules that each 802.1X port acl requires.
- For the rule 'name' field, please do not use 'd1x' or 'redir41x' as rules which match are treated specially internally.
- Change the mac address '08:00:27:00:03:02' to the mac address of the server that hostap is running on.
It should be possible to run multiple hostap servers and load balance them via changing the 'actions: dl_dst: <mac_address>' of some of the port acls (untested).

##### HTTPServer.py
The faucet repository contains HTTPServer.py which is used as the 'proxy' between the authentication servers and faucet.
This must run on the same machine as faucet.

auth.yaml is the configuration file and contains annotations on required parts. Note: the structure and content is subject to change.


### Running

#### Controller

##### Faucet

To start faucet run:
```bash
$ export FAUCET_CONFIG=/home/ubuntu/faucet-dev/faucet.yaml
$ export GAUGE_CONFIG=/etc/ryu/faucet/gauge.yaml
$ export FAUCET_LOG=/var/log/faucet/faucet.log
$ export FAUCET_EXCEPTION_LOG=/var/log/faucet/faucet_exception.log
$ export GAUGE_LOG=/var/log/faucet/gauge_exception.log
$ export GAUGE_EXCEPTION_LOG=/var/log/faucet/gauge_exception.log
$ export GAUGE_DB_CONFIG=/etc/ryu/faucet/gauge_db.yaml

$ ryu-manager faucet.faucet
```
changing directories as required.

##### HTTPServer
To start the httpserver run:
```hash
$ cd faucet/
$ python3 faucet/HTTPServer.py --config auth.yaml
```
#### Authentication Server

To start hostapd run as sudo:
```bash
hostapd wired.conf
```

Start the RADIUS server according to your implementations instructions.

## Captive Portal
Not Implemented yet.
### Componets
- Captive Portal Webserver
- RADIUS Server
- Faucet
- HTTPServer

# TODO

- allow user to have their own rules on the port before our user is authenticated ones and after the 1x to hostapd.
For example if all traffic from port is not allowed to go to 8.8.8.8 for what ever reason.i

- allow the use of 3 modes; 802.1X, Captive Portal, 802.1X with Captive Portal fallback on a port (not necessarily 1X).