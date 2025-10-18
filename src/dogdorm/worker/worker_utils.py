import httpx
from p2pd import *
from ..defs import *

async def validate_stun_server(ip, port, pipe, mode, cip=None, cport=None):
    # New client used for the req.
    stun_client = STUNClient(
        af=pipe.route.af,
        dest=(ip, port),
        nic=pipe.route.interface,
        proto=pipe.proto,
        mode=mode
    )

    # Lowever level -- get STUN reply.
    reply = None
    if mode == RFC3489:
        # Reply from different port only.
        if cport is not None and cip is None:
            #print("change port", ip, ":", cport)
            reply = await stun_client.get_change_port_reply((ip, cport), pipe)
            

        # Reply from different IP:port only.
        if cip is not None and cport is not None:
            #print("change ip:port", cip, ":", cport)
            reply = await stun_client.get_change_tup_reply((cip, cport), pipe)

        # The NAT test code doesn't need to very just the IP.
        # So that edge case is not checked.
        # if cip is not None and cport is None: etc
        if cip is None and cport is None:
            #print("get reug stun reply.")
            reply = await stun_client.get_stun_reply(pipe=pipe)
    else:
        reply = await stun_client.get_stun_reply(pipe=pipe)
    
    # Validate the reply.
    reply = validate_stun_reply(reply, mode)
    if reply is None:
        raise Exception("Invalid stun reply.")

    return reply

async def stun_server_classifier(af, ip, port, nic):
    # List of STUN server endpoints sorted based on type and proto.
    servers = []

    # Mostly RFC3489 is used for NAT checks whick need UDP.
    # Also, its assumed that IPv4 is used since NATs are used there.
    # Though you can also NAT on v6.
    try:
        # Initial STUN client used to check if a server can support NAT tests.
        route = nic.route(af)
        pipe = await pipe_open(UDP, route=route)
        stun_client = STUNClient(
            af=pipe.route.af,
            dest=(ip, port),
            nic=pipe.route.interface,
            proto=pipe.proto,
            mode=RFC3489
        )

        # Get initial reply from STUN server.
        # The reply needs the change port and change IP attribytes.
        reply = await stun_client.get_stun_reply(pipe=pipe)
        reply = validate_stun_reply(reply, RFC3489)
        if reply is not None:
            primary_tup = (ip, port, reply.ctup[1],)
            secondary_tup = (reply.ctup[0], port, reply.ctup[1],)

            # Throws exception on failure.
            await validate_rfc3489_stun_server(
                af,
                UDP,
                nic,
                primary_tup,
                secondary_tup
            )

            servers.append([
                [STUN_CHANGE_TYPE, int(af), int(UDP), ip, port, None, None],
                [STUN_CHANGE_TYPE, int(af), int(UDP), ip, reply.ctup[1], None, None],
                [STUN_CHANGE_TYPE, int(af), int(UDP), reply.ctup[0], port, None, None],
                [STUN_CHANGE_TYPE, int(af), int(UDP), reply.ctup[0], reply.ctup[1], None, None]
            ])
    except:
        log_exception()

    # We specifically DO NOT add any potential change IPs into map.
    # Otherwise WAN IP lookups can contaminate NAT test results.
    # TODO: Perhaps the DB could have a special trigger for this?
    stun_infos  = [
        #(TCP, RFC3489, STUN_CHANGE_TYPE),
        (TCP, RFC5389, STUN_MAP_TYPE),
        (UDP, RFC5389, STUN_MAP_TYPE)
    ]

    # Check other capabilities for STUN server.
    # Here the RFC type controls whether to send a specfic magic cookie.
    # It says "change type" but here we're only interest in a reply at all
    for stun_info in stun_infos:
        stun_proto, stun_mode, stun_type = stun_info
        stun_client = STUNClient(
            af=af,
            dest=(ip, port),
            nic=nic,
            proto=stun_proto,
            mode=stun_mode
        )

        try:
            wan_ip = await stun_client.get_wan_ip()
            if wan_ip is not None:
                servers.append([
                    [stun_type, int(af), int(stun_proto), ip, port, None, None]
                ])
        except:
            log_exception()
            continue

    return servers

# So with RFC 3489 there's actualoly 4 STUN servers to check:
async def validate_rfc3489_stun_server(af, proto, nic, primary_tup, secondary_tup):
    infos = [
        # Test primary ip, port.
        (primary_tup[0], primary_tup[1], None, None,),

        # Test reply from primary ip, change port.
        (primary_tup[0], primary_tup[1], None, primary_tup[2],),

        # Test primary ip, change ip replay.
        # NAT test doesn't need this functionality -- skip for now.
        #(secondary_tup[0], secondary_tup[1], secondary_tup[0], None),

        # Test secondary IP, change port.
        (primary_tup[0], primary_tup[1], secondary_tup[0], secondary_tup[2],),
    ]

    route = nic.route(af)
    pipe = await pipe_open(proto, route=route)

    # Compare IPS in different tups (must be different)
    if IPR(primary_tup[0], af) == IPR(secondary_tup[0], af):
        raise Exception("primary and secondary IPs must differ 3489.")

    # Change port must differ.
    if primary_tup[1] == secondary_tup[2]:
        raise Exception("change port must differ 3489")

    # Test each STUN server.
    for info in infos:
        dest_ip, dest_port, cip, cport = info
        await validate_stun_server(
            ip=dest_ip,
            port=dest_port,
            pipe=pipe,
            mode=RFC3489,
            cip=cip,
            cport=cport
        )

# Will just have workers wait until success.
async def retry_curl_on_locked(curl, params, endpoint, retries=3):
    url = "http://localhost:8000" + endpoint
    async with httpx.AsyncClient() as client:
        while retries is None or retries > 0:
            # Decrement sentinel.
            if retries is not None:
                retries -= 1

            # Make the request.
            response = await client.post(url, json=params)

            # Server down, try again.
            if response.status_code is not 200:
                await sleep_random(1000, 3000)
                continue

            # Return output.
            print(response.json())
            return response.json()

async def fetch_work_list(curl, table_type=None):
    nic = curl.route.interface
    work = []

    # Fetch work from dealer server.
    params = {
        "stack_type": int(nic.stack),
        "table_type": table_type,
        "current_time": None,
        "monitor_frequency": None
    }
    resp = await retry_curl_on_locked(curl, params, "/work")
    if resp is None:
        return INVALID_SERVER_RESPONSE


    # Wrap in try except for safety:
    # Server might return an unexpected response.
    try:
        work = resp
        f = lambda r: r["id"]
        work = sorted(work, key=f)
        for grouped in work:
            if hasattr(grouped, "af"):
                grouped["af"] = IP4 if grouped["af"] == 2 else IP6

            if hasattr(grouped, "proto"):
                grouped["proto"] = UDP if grouped["proto"] == 2 else TCP
    except:
        print("Could not process server resp as work " + to_s(resp.out))
        what_exception()
        return INVALID_SERVER_RESPONSE

    # Return work (may exist or not.)
    return work

async def update_work_status(curl, status_ids, is_success):
    # Indicate the status outcome.
    t = int(time.time())
    statuses = []
    for status_id in status_ids:
        params = {"is_success": int(is_success), "status_id": status_id, "t": t}
        statuses.append(params)

    if len(statuses):
        params = {"statuses": statuses}
        await retry_curl_on_locked(curl, params, "/complete")
        #print(out.out)

async def validate_service_import(nic, pending_insert, service_monitor):
    import_list = []
    if pending_insert["type"] == STUN_MAP_TYPE:
        import_list = await stun_server_classifier(
            af=pending_insert["af"],
            ip=pending_insert["ip"],
            port=pending_insert["port"],
            nic=nic
        )
    else:
        # Reuse the existing code for validation.
        is_success = await service_monitor(nic, [pending_insert])
        service_type = pending_insert["type"]
        if service_type in (MQTT_TYPE, NTP_TYPE, TURN_TYPE,):
            proto = UDP
        else:
            proto = TCP

        if is_success:
            # Same format as what stun_server_classifier returns.
            import_list = [[
                [
                    pending_insert["type"], 
                    pending_insert["af"],
                    proto,
                    pending_insert["ip"],
                    pending_insert["port"],
                    pending_insert["user"],
                    pending_insert["password"],
                ]
            ]]

    return import_list