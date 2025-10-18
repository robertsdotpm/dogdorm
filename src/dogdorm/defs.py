from p2pd import *
import os
from typing import Any, List, Optional
from pydantic import BaseModel


DB_NAME = os.path.join(get_script_parent(), "db", "monitor.sqlite3")
WORKER_TIMEOUT = 120
MONITOR_FREQUENCY = 60 * 60
MONITOR_FREQUENCY = 60 * 60 # Just temp for testing.
MAX_SERVER_DOWNTIME = 600
IMPORT_TEST_NO = 3 # Try to import items 3 times then stop.

####################################################################################
SERVICE_SCHEMA = ("type", "af", "proto", "ip", "port", "group_id")
STATUS_SCHEMA = ("row_id", "table_type", "status", "last_status", "test_no")
STATUS_SCHEMA += ("failed_tests", "last_success", "last_uptime")
STUN_MAP_TYPE = 3
STUN_CHANGE_TYPE = 4
MQTT_TYPE = 5
TURN_TYPE = 6
NTP_TYPE = 7
PNP_TYPE = 8
SERVICE_TYPES  = (STUN_MAP_TYPE, STUN_CHANGE_TYPE, MQTT_TYPE,)
SERVICE_TYPES += (TURN_TYPE, NTP_TYPE)

STATUS_AVAILABLE = 9
STATUS_DEALT = 11
STATUS_INIT = 12
STATUS_DISABLED = 13
STATUS_TYPES = (STATUS_INIT, STATUS_AVAILABLE, STATUS_DEALT, STATUS_DISABLED,)
SERVICES_TABLE_TYPE = 14
ALIASES_TABLE_TYPE = 15
IMPORTS_TABLE_TYPE = 16
GROUPS_TABLE_TYPE = 17
STATUS_TABLE_TYPE = 18
NO_WORK = -1
INVALID_SERVER_RESPONSE = -2
TABLE_TYPES = (SERVICES_TABLE_TYPE, ALIASES_TABLE_TYPE, IMPORTS_TABLE_TYPE,)

SERVICE_LOOKUP = {
    "stun": STUN_MAP_TYPE,
    "mqtt": MQTT_TYPE,
    "turn": TURN_TYPE,
    "ntp": NTP_TYPE,
}

class DuplicateRecordError(KeyError):
    """Raised when a duplicate key is inserted."""
    pass

