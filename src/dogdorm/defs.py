from p2pd import *
import os

####################################################################################

# Work can be handed back out after this.
WORKER_TIMEOUT = 120

# Servers are checked this often.
MONITOR_FREQUENCY = 60 * 60

# DNS IPs for services are only updated after N secs of downtime.
MAX_SERVER_DOWNTIME = 600

# Try to import items 3 times then stop.
IMPORT_TEST_NO = 3 

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

