import asyncio
from p2pd import *
from ..defs import *
from .worker_utils import *

async def monitor_stun_map_type(nic, work):
    client = STUNClient(
        work[0]["af"],
        (work[0]["ip"], work[0]["port"],),
        nic,
        proto=work[0]["proto"],
        mode=RFC5389
    )

    out = await client.get_wan_ip()
    return 1

async def monitor_stun_change_type(nic, work):
    # Validates the relationship between 4 stun servers.
    await validate_rfc3489_stun_server(
        work[0]["af"],
        work[0]["proto"],
        nic,

        # IP, main port, secondary port
        (work[0]["ip"], work[0]["port"], work[1]["port"],),
        (work[2]["ip"], work[2]["port"], work[3]["port"],),
    )

    return 1

async def monitor_mqtt_type(nic, work):
    found_msg = asyncio.Queue()

    def mqtt_proto_closure(ret):
        async def mqtt_proto(payload, client):
            found_msg.put_nowait(payload)

        return mqtt_proto

    # Setup MQTT client with basic proto.
    mqtt_proto = mqtt_proto_closure(found_msg)
    peer_id = to_s(rand_plain(10))
    dest = (work[0]["ip"], work[0]["port"])
    client = SignalMock(peer_id, mqtt_proto, dest)

    # Send message to self and try receive it.
    client = await client.start()
    for i in range(0, 3):
        await client.send_msg(peer_id, peer_id)

        # Allow time to receive responds.
        await asyncio.sleep(0.1)
        if not found_msg.empty(): break

    # Wait for a reply.
    try:
        await asyncio.wait_for(found_msg.get(), 1.0)
        return 1
    except asyncio.TimeoutError:
        return 0

async def monitor_turn_type(nic, work):
    user = "" if work[0]["user"] is None else work[0]["user"]
    password = "" if work[0]["password"] is None else work[0]["password"]
    client = await TURNClient(
        af=work[0]["af"],
        dest=(work[0]["ip"], work[0]["port"]),
        nic=nic,
        auth=(user, password),

        # No realm support for now. Most don't set it.
        realm=None
    )

    if client:
        r_addr, r_relay = await client.get_tups()
        await client.close()

        if None not in (r_addr, r_relay):
            return 1

    return 0

async def monitor_ntp_type(nic, work):
    try:
        for _ in range(3):
            client = NTPClient(nic)
            response = await client.request(
                (work[0]["ip"], work[0]["port"]),
                version=3
            )
            if response is None:
                continue

            return 1
    except Exception as e:
        log_exception()

    return 0

async def service_monitor(nic, work):
    is_success = 0
    work_type = work[0]["type"]

    if len(work) == 1:
        if work_type == STUN_MAP_TYPE:
            is_success = await monitor_stun_map_type(nic, work)

        if work_type == MQTT_TYPE:
            is_success = await monitor_mqtt_type(nic, work)

        if work_type == TURN_TYPE:
            is_success = await monitor_turn_type(nic, work)

        if work_type == NTP_TYPE:
            is_success = await monitor_ntp_type(nic, work)

    if len(work) == 4:
        if work_type == STUN_CHANGE_TYPE:
            is_success = await monitor_stun_change_type(nic, work)
    
    return is_success

async def imports_monitor(nic, pending_insert):
    validated_lists = await validate_service_import(
        nic,
        pending_insert[0],
        service_monitor
    )

    if pending_insert[0]["alias_id"] is not None:
        alias_id = int(pending_insert[0]["alias_id"])
    else:
        alias_id = None

    # Create a list of groups (a group can have one or more related services.)
    imports_list = []
    
    for validated_list in validated_lists:
        services = []
        for server in validated_list:
            print(validated_list)
            if server[0] is None:
                continue

            services.append({
                "service_type": int(server[0]),
                "af": int(server[1]),
                "proto": int(server[2]),
                "ip": server[3],
                "port": int(server[4]),
                "user": server[5],
                "password": server[6],
                "alias_id": alias_id,
                "score": 0
            })

        imports_list.append(services)

    return imports_list

    # Nothing to import.
    if not imports_list:
        return 0



    # Same return time but update status handled by /insert.
    return 1

async def alias_monitor(curl, alias):
    nic = curl.route.interface
    try:
        addr = await Address(alias[0]["fqn"], 80, nic)
        ip = addr.select_ip(alias[0]["af"]).ip
        return ip
    except:
        return 0