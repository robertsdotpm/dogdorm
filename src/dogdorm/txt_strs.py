from p2pd import UDP, TCP, V4, V6
from .defs import *


TXTS = {
    "af": {
        V4: "IPv4",
        V6: "IPv6",
    },
    "proto": {
        TCP: "TCP",
        UDP: "UDP",
    },
    STUN_MAP_TYPE: "STUN(see_ip)",
    STUN_CHANGE_TYPE: "STUN(test_nat)",
    MQTT_TYPE: "MQTT",
    TURN_TYPE: "TURN",
    NTP_TYPE: "NTP",
    PNP_TYPE: "PNP",
    STATUS_AVAILABLE: "Available",
    STATUS_DEALT: "Dealt",
    STATUS_INIT: "Init",
    STATUS_DISABLED: "Disabled",
    SERVICES_TABLE_TYPE: "services",
    ALIASES_TABLE_TYPE: "aliases",
    IMPORTS_TABLE_TYPE: "imports",  
}

