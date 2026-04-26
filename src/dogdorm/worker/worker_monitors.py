import asyncio
from p2pd import *
from ..defs import *
from .worker_utils import *

"""
The most recent STUN standard: RFC 5389 removes the ability to
specify STUN replies from a different IP and/or port.
Thus, "map" servers can only be used to lookup a WAN IP.
The mode here really just changes the magic cookie field in
the STUN packet (and its size.)
"""
async def monitor_stun_map_type(nic, work):
    # Spawn a new STUN client set to the server details.
    client = STUNClient(
        work[0]["af"],
        (work[0]["ip"], work[0]["port"],),
        nic,
        proto=work[0]["proto"],
        mode=RFC5389
    )

    # Attempt to get external IP using STUN.
    out = await client.get_wan_ip()
    if out is not None:
        return 1
    else:
        return 0

"""
RFC 3489 STUN servers are those that support replies from a different
IP or port. The standard was deprecated because it didn't work well enough
on enterprise / business networks. However, its still very important for
peer-to-peer networks as residential routers are actually consistent.
These servers allow for NAT tests to be done.
"""
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

"""
MQTT is a light-weight protocol that supports pub-sub. Its offered over
different transports and can support encryption. However, for this software
we're only interested in UDP. MQTT is used as a way to send initial messages
to peers behind NAT devices who can then coordinate a strategy to achieve
direct connections between them.
"""
async def monitor_mqtt_type(nic, work):
    dest = (work[0]["ip"], work[0]["port"])
    client = await is_valid_mqtt(dest)
    if client:
        await client.close()
        return 1
    else:
        return 0

"""
TURN is a type of proxy service devised to be used in environments
where direct connectivity between peers has failed. It is used in
webrtc as a fallback when UDP hole punching has failed. TURN supports TCP
but the way it offers it is not useful as a fallback due to the fact that
the tunnel destination needs to be reachable. So instead, this software
only monitors TURN servers that support UDP.

The TURN client in P2PD is only tested with IPv4. It might be possible to
add IPv6 in the future, but in practice there are almost no IPv6 public
TURN servers. So this is a low priority for now.
"""
async def monitor_turn_type(nic, work):
    """End-to-end TURN check: ALLOCATE + CreatePermission + relay round-trip.

    The earlier shape only verified ALLOCATE -- a server could happily
    hand out a relay tup and still drop traffic at the CreatePermission
    or relay-forward stages. p2pd's flake-tracing showed exactly this:
    a server (47.96.130.35 / gitclone.com) kept its score >= 0.89 in
    servers.json while it was actually broken at the relay stage,
    because the monitor never sent a byte across it.

    Full check: spin up two TURNClient instances against this server,
    cross-register them as each other's peer, send a probe payload
    from A -> B and wait for B to receive it. Each phase is bounded
    so a hung server can't eat the worker budget.

    Phase return codes (0 == fail, 1 == ok); the caller stores 1/0.
    The exact failing phase is logged via log() so the dealer's
    history shows where servers commonly break (allocate vs
    create-permission vs relay-recv).
    """
    user = "" if work[0]["user"] is None else work[0]["user"]
    password = "" if work[0]["password"] is None else work[0]["password"]
    server_label = "{0}:{1}".format(work[0]["ip"], work[0]["port"])

    payload = b"DOGDORM-TURN-PROBE"
    received = asyncio.Event()
    received_data = []

    def on_b_msg(msg, client_tup, pipe):
        received_data.append(msg)
        if msg and payload in msg:
            received.set()

    client_a = TURNClient(
        af=work[0]["af"],
        dest=(work[0]["ip"], work[0]["port"]),
        nic=nic,
        auth=(user, password),
        realm=None,
    )
    client_b = TURNClient(
        af=work[0]["af"],
        dest=(work[0]["ip"], work[0]["port"]),
        nic=nic,
        auth=(user, password),
        realm=None,
        msg_cb=on_b_msg,
    )

    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(client_a.start(), client_b.start()),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log("TURN monitor {0} fail phase=allocate".format(server_label))
            return 0
        except (OSError, ConnectionError):
            log_exception()
            log("TURN monitor {0} fail phase=allocate-os".format(server_label))
            return 0

        try:
            a_peer = await client_a.client_tup_future
            a_relay = await client_a.relay_tup_future
            b_peer = await client_b.client_tup_future
            b_relay = await client_b.relay_tup_future
        except (asyncio.CancelledError):
            raise
        except Exception:
            log_exception()
            log("TURN monitor {0} fail phase=tup-future".format(server_label))
            return 0

        if None in (a_peer, a_relay, b_peer, b_relay):
            log("TURN monitor {0} fail phase=tup-missing".format(server_label))
            return 0

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    client_a.accept_peer(b_peer, b_relay),
                    client_b.accept_peer(a_peer, a_relay),
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            log("TURN monitor {0} fail phase=create-permission".format(server_label))
            return 0
        except (OSError, ConnectionError):
            log_exception()
            log("TURN monitor {0} fail phase=create-permission-os".format(server_label))
            return 0

        try:
            await client_a.send(payload, dest_tup=b_peer)
        except (OSError, ConnectionError, ValueError):
            log_exception()
            log("TURN monitor {0} fail phase=send".format(server_label))
            return 0

        try:
            await asyncio.wait_for(received.wait(), timeout=8)
        except asyncio.TimeoutError:
            log("TURN monitor {0} fail phase=relay-recv (got={1} chunks)".format(
                server_label, len(received_data),
            ))
            return 0

        if not any(payload in m for m in received_data if m):
            log("TURN monitor {0} fail phase=relay-payload-missing".format(server_label))
            return 0

        log("TURN monitor {0} ok".format(server_label))
        return 1
    finally:
        for c in (client_a, client_b):
            try:
                await asyncio.wait_for(c.close(), timeout=5)
            except Exception:
                pass

"""
NTP is a protocol to try to maintain somewhat accurate, universal time,
over the Internet. It's used to provide absolute references times to
synchronize TCP hole punching in P2PD.
"""
async def monitor_ntp_type(nic, work):
    try:
        # Resolved to the server address.
        server = {
            "host": work[0]["ip"],
            "port": work[0]["port"]
        }
        
        # Use small helper func in clock_skew p2pd module.
        response = await get_ntp(
            work[0]["af"],
            nic, 
            server=server
        )

        # Dec on sec, None on failure.
        if response:
            return 1
    except Exception as e:
        log_exception()

    return 0

# Check whether a server is alive.
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
    """
    Give a possible server to import: check to see if it's alive.
    This may yield multiple related services to import if they're
    on different AFs and protocols that the software is interested in.
    A "discovery" step is done on the imports to yield the bellow list.
    """
    validated_lists = await validate_service_import(
        nic,
        pending_insert[0],
        service_monitor
    )

    # Associate status with an alias if it was set.
    if pending_insert[0]["alias_id"] is not None:
        alias_id = int(pending_insert[0]["alias_id"])
    else:
        alias_id = None

    # Create a list of groups (a group can have one or more related services.)
    imports_list = []
    for validated_list in validated_lists:
        services = []
        for server in validated_list:
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

    # May be empty on failure.
    return imports_list

"""
The software can also lookup DNS names to update IPs if they change.
Support for IPv4 and 6 works here.
"""
async def alias_monitor(curl, alias):
    nic = curl.route.interface

    # Resolve a DNS name and index by AF.
    # First IP for the AF is used if it has multiple.
    try:
        addr = await Address(alias[0]["fqn"], 80, nic)
        ip = addr.select_ip(alias[0]["af"]).ip
        return ip
    except:
        return 0