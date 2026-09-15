from p2pd import *
import os

####################################################################################

# Work can be handed back out after this.
WORKER_TIMEOUT = 120

# The longest a worker spends on one piece of work before giving up on it.
# Kept under WORKER_TIMEOUT so the failure is reported before the dealer
# hands the same work to someone else.
WORK_TIMEOUT = WORKER_TIMEOUT - 30

# Servers are checked this often.
MONITOR_FREQUENCY = 60 * 60 * 4

"""
Each next check lands anywhere within this fraction of MONITOR_FREQUENCY
either side of it -- three to five hours for a four hour frequency.

Without it, servers checked together stay together: anything that makes the
whole fleet due at once (a first start, catching up after an outage) turns
into the same burst every four hours from then on. A random offset each time
smears them out over a few cycles, and the average frequency stays the same.
"""
SCHEDULE_JITTER = 0.25

# DNS IPs for services are only updated after N secs of downtime.
MAX_SERVER_DOWNTIME = 600

# Try to import items 3 times then stop.
IMPORT_TEST_NO = 3 

"""
Where the dealer listens, and where the workers look for it.

The bind host defaults to every interface, which is what a plain install
wants. A deployment that puts a web server in front of the dealer sets
DOGDORM_DEALER_BIND_HOST=127.0.0.1 and moves the port, so the only thing on
the public port is the web server -- which can then serve the published list
off disk whether the dealer is up or not.
"""
DEALER_BIND_HOST = os.environ.get("DOGDORM_DEALER_BIND_HOST", "*")
DEALER_HOST = os.environ.get("DOGDORM_DEALER_HOST", "127.0.0.1")
DEALER_PORT = int(os.environ.get("DOGDORM_DEALER_PORT", "8000"))

"""
The dealer writes the finished server list here every time it rebuilds it,
so a web server can serve that file directly. The point is that /servers then
does not depend on the dealer being alive: it survives a restart, and a crash.

Set to "" to turn publishing off.
"""
SERVERS_FILE = os.environ.get("DOGDORM_SERVERS_FILE", "/opt/dogdorm/servers.json")

"""
Groups repopulate over a minute or two after a restart, so a freshly built
list can briefly be missing a chunk of its servers -- on the P2PD monitor it
dipped from 948 to 826 before recovering. Publishing that would advertise
fewer servers than really exist, so a list that lost more than this fraction
of the last published one is held back until the grace period is up, by which
point a real loss is real.
"""
PUBLISH_SHRINK_FLOOR = 0.9
PUBLISH_SHRINK_GRACE = 20 * 60

# A monitored server that has not answered for this long is dead rather than
# flaky, so it stops being handed out as work. At MONITOR_FREQUENCY that is
# around 84 consecutive failures before anything is retired.
RETIRE_AFTER = 14 * 24 * 60 * 60

# One that has never answered at all has no last_success to measure from, so
# it is retired on how many times we have tried instead.
RETIRE_NEVER_AFTER_TESTS = 20

"""
A retired server is left out of the published list and not checked every
MONITOR_FREQUENCY -- but not given up on either. It is tried again this often;
if it answers it goes straight back into rotation and back into the list,
and if not it waits for the next try. Imports are not included: an import is
disabled because it is finished, not because it went quiet.
"""
RETIRED_RECHECK = 7 * 24 * 60 * 60

# Where a browser landing on "/" gets sent. What the dealer serves is JSON
# meant for other programs; the dashboard is the readable view of the same
# data. Set it to "" to leave "/" alone, or point it at your own page.
ROOT_REDIRECT = os.environ.get(
    "DOGDORM_ROOT_REDIRECT",
    "https://www.warpgate.io/netstats.html",
)

"""
Manually cache your NIC details here using
python3 -m p2pd

nic = await Interface()
await nic.load_nat()
print(nic.to_dict())
"""
IF_INFO = {'id': 'eno1',
 'is_default': {2: True, 10: True},
 'mac': '00-1e-67-fa-5d-42',
 'name': 'eno1',
 'nat': {'delta': {'type': 1, 'value': 0},
         'delta_info': 'not applicable',
         'nat_info': 'open internet',
         'type': 1},
 'netiface_index': 1,
 'nic_no': 0,
 'rp': {2: [{'af': 2,
             'ext_ips': [{'af': 2, 'cidr': 32, 'ip': '158.69.27.176'}],
             'link_local_ips': [],
             'nic_ips': [{'af': 2, 'cidr': 32, 'ip': '158.69.27.176'}]}],
        10: [{'af': 10,
             'ext_ips': [{'af': 10, 'cidr': 128, 'ip': '2607:5300:60:80b0::1'}],
             'link_local_ips': [],
             'nic_ips': [{'af': 10, 'cidr': 128, 'ip': '2607:5300:60:80b0::1'}]}]
        }
}

####################################################################################

# Used to back up the memory database to sqlite.
DB_NAME = os.path.join(get_script_parent(), "db", "monitor.sqlite3")

# These enums are all the types of servers that can be monitored.
STUN_MAP_TYPE = 3
STUN_CHANGE_TYPE = 4
MQTT_TYPE = 5
TURN_TYPE = 6
NTP_TYPE = 7
PNP_TYPE = 8
SERVICE_TYPES  = (STUN_MAP_TYPE, STUN_CHANGE_TYPE, MQTT_TYPE,)
SERVICE_TYPES += (TURN_TYPE, NTP_TYPE)

# The work queues used to allocate work.
STATUS_AVAILABLE = 9
STATUS_DEALT = 11
STATUS_INIT = 12
STATUS_DISABLED = 13
STATUS_TYPES = (STATUS_INIT, STATUS_AVAILABLE, STATUS_DEALT, STATUS_DISABLED,)

# Specific categories of work.
SERVICES_TABLE_TYPE = 14
ALIASES_TABLE_TYPE = 15
IMPORTS_TABLE_TYPE = 16
GROUPS_TABLE_TYPE = 17
STATUS_TABLE_TYPE = 18
TABLE_TYPES = (SERVICES_TABLE_TYPE, ALIASES_TABLE_TYPE, IMPORTS_TABLE_TYPE,)

# Error messages.
NO_WORK = -1
INVALID_SERVER_RESPONSE = -2

class DuplicateRecordError(KeyError):
    """Raised when a duplicate key is inserted."""
    pass

