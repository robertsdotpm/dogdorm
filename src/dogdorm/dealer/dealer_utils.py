import math
import random
import time
import json
from fastapi.responses import JSONResponse
from fastapi import Request, HTTPException
from p2pd import *
from ..defs import *
from ..txt_strs import *
from ..db.db_init import *

# Limit API method to localhost clients only.
def localhost_only(request: Request):
    client_host = request.client.host
    if client_host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Access forbidden")

# Indicate an API response is JSON (and format it nicely.)
class PrettyJSONResponse(JSONResponse):
    def render(self, content: any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,        # pretty-print here
        ).encode("utf-8")

"""
The software monitors uptime of servers, failures, and so on.
Based on these variables a score can be computed for the server to
try to reflect its overall reliability. The servers are then filtered
by this score so that the most reliable servers are first.
"""
def compute_service_score(status, max_uptime_override=None):
    if not isinstance(status, dict) or status is None:
        return 0.0

    # Extract values, default to 0 if missing or None
    failed_tests = status.get("failed_tests") or 0
    test_no = status.get("test_no") or 0
    uptime = status.get("uptime") or 0
    if max_uptime_override is not None:
        max_uptime = max_uptime_override
    else:
        if "max_uptime" in status and status["max_uptime"] is not None:
            max_uptime = status["max_uptime"]
        else:
            max_uptime = 0

    # Prevent negative numbers
    failed_tests = max(failed_tests, 0)
    test_no = max(test_no, 0)
    uptime = max(uptime, 0)
    max_uptime = max(max_uptime, 0)

    # Compute uptime ratio safely
    uptime_ratio = (uptime / max_uptime) if max_uptime > 0 else 0.0
    uptime_ratio = min(max(uptime_ratio, 0.0), 1.0)

    # Compute test factor safely
    test_factor = 1.0 - failed_tests / (test_no + 1e-9)
    smoothing_factor = 1.0 - math.exp(-test_no / 50.0)
    quality_score = test_factor * (0.5 * uptime_ratio + 0.5) * smoothing_factor

    # Clamp final score to [0,1]
    return min(max(quality_score, 0.0), 1.0)

"""
When servers are imported a DNS or FQN may be associated with them.
That DNS name helps to make sure that if the IP ever changes the software
can find where the new server is in future. Not all IPs added will have FQNs
associated with them but if any FQNs "point" to that IP it will show up here.
"""
def get_fqn_list(mem_db, ip):
    if ip is None:
        return []
    
    fqns = set()
    if ip in mem_db.aliases_by_ip:
        for alias in mem_db.aliases_by_ip[ip]:
            if alias.fqn is not None:
                fqns.add(alias.fqn)

    return list(fqns)[::-1]

"""
This function builds the result list for the /server call.
Every single relevant record is pulled and put into a single dict.
Importantly, the fields are sorted to hopefully reflect the most reliable
servers first in the results.
"""
def build_server_list(mem_db):
    # Init server list
    s = {}
    for service_type in SERVICE_TYPES:
        by_service = s[TXTS[service_type]] = {}
        for af in VALID_AFS:
            by_af = by_service[TXTS["af"][af]] = {}
            for proto in (UDP, TCP):
                by_proto = by_af[TXTS["proto"][proto]] = []

    for group_id in mem_db.groups:
        try:
            # A group is one or more associated servers.
            # Only STUN has more than one so far (test_NAT.)
            meta_group = mem_db.groups[group_id]
            if meta_group.table_type != SERVICES_TABLE_TYPE:
                continue

            # Retired servers are not published: a list of servers to use
            # should not lead with ones that stopped answering weeks ago.
            # They are still checked weekly and reappear once they answer.
            queued = mem_db.work[SERVICES_TABLE_TYPE][meta_group.af].index.get(group_id)
            if queued and queued[0] == STATUS_DISABLED:
                continue

            # Avoid local IPs.
            group = list_x_to_dict(meta_group.group)
            localhosts = ("127.0.0.1", "0000:0000:0000:0000:0000:0000:0000:0001",)
            skip_group = False
            for record in group:
                if record["ip"] in localhosts:
                    skip_group = True
                    break
            
            # Skip group with junk in it.
            if skip_group:
                continue

            # Combine associated status fields with record table field.
            scores = []
            fields = ("test_no", "failed_tests", "uptime", "max_uptime", "last_success")
            for record in group:
                # Invalid records should not break the entire attempt.
                try:
                    # If there's no associated status record then skip.
                    status_obj = mem_db.statuses.get(record.get("status_id"))
                    if not status_obj:
                        continue
                    status = getattr(status_obj, "dict", lambda: {})()

                    # Combine status fields with record.
                    for k in fields:
                        record[k] = status.get(k, 0)

                    # Computer score and add fqn.
                    record["score"] = compute_service_score(status)
                    record["fqns"] = get_fqn_list(mem_db, record.get("ip"))
                    scores.append(record["score"])
                except Exception:
                    # Skip invalid record but continue processing others
                    continue

            # Since a group may have multiple servers (and different scores)
            # to simply sorting we average them and set the same for all entries.
            if scores:
                score_avg = sum(scores) / len(scores)
                for record in group:
                    record["score"] = score_avg

            # Place group in server list
            if group:
                service_type = TXTS.get(group[0].get("type"), "unknown")
                af = TXTS["af"].get(group[0].get("af"), "unknown")
                proto = TXTS["proto"].get(group[0].get("proto"), "unknown")
                s.setdefault(service_type, {}).setdefault(af, {}).setdefault(proto, []).append(group)

        except Exception:
            # Skip invalid group entirely
            continue

    # Sort each proto list by score
    for service_type in SERVICE_TYPES:
        for af in VALID_AFS:
            for proto in (UDP, TCP):
                """
                The most import line here is the sort line:
                it sorts by group since every entry in the group gets the same score
                the group items don't move, but the groups as a whole do to reflect
                where groups fall with respect to each other.
                """
                try:
                    by_proto = s[TXTS[service_type]][TXTS["af"][af]][TXTS["proto"][proto]]
                    by_proto.sort(key=lambda x: x[0].get("score", 0), reverse=True)
                except Exception:
                    continue

    # Indicate how fresh the results are.
    s["timestamp"] = int(time.time())
    return s

"""
Used by the server to indicate that work handed out has been "done"
and they are updating the status of the result.
Work jobs may succeed or fail.
"""
"""
A server that has not answered for RETIRE_AFTER is not flaky, it is gone.
Leaving it in rotation spends a check on it every MONITOR_FREQUENCY forever,
which is traffic aimed at someone else's infrastructure for no reason. The
decision is made on the status row we already keep: when it last answered, or
failing that, how many times we have asked.
"""
def is_retired(status, t):
    if status.last_success:
        return (t - status.last_success) > RETIRE_AFTER

    # Never answered once. Judge it on how many attempts it has had.
    return status.test_no >= RETIRE_NEVER_AFTER_TESTS

# When work next becomes due is its queue time plus MONITOR_FREQUENCY, so
# shifting the queue time shifts the check. last_status keeps the real time.
def jittered(t):
    return int(t + random.uniform(-SCHEDULE_JITTER, SCHEDULE_JITTER) * MONITOR_FREQUENCY)

def mark_complete(mem_db, is_success: int, status_id: int, t=None):
    # Work starts out with the target of being reassigned available.
    t = t or int(time.time())
    status_type = STATUS_AVAILABLE
    if status_id not in mem_db.statuses:
        raise KeyError("could not load status row %s" % (status_id,))
    
    status = mem_db.statuses[status_id]
    table_type = status.table_type

    # Remove from dealt queue.
    record = mem_db.records[table_type][status.row_id]
    af = record.af
    group_id = record.group_id

    # Update stats for success.
    if is_success:
        if not status.last_uptime:
            change = 0
        else:
            change = max(0, t - status.last_uptime)

        status.uptime += change
        if status.uptime > status.max_uptime:
            status.max_uptime = status.uptime

        status.last_uptime = t
        status.last_success = t

    # Update stats for failure.
    if not is_success:
        status.failed_tests += 1
        status.uptime = 0

    status.test_no += 1
    status.last_status = t

    """
    Where the work goes next, decided on the stats as they now stand.

    Imports are one-shot: a success means there is nothing left to import,
    and enough failures mean it was never going to work.
    """
    if table_type == IMPORTS_TABLE_TYPE:
        if is_success or status.test_no >= IMPORT_TEST_NO:
            status_type = STATUS_DISABLED

    # A service or alias that has stopped answering for long enough is
    # retired rather than checked forever.
    if table_type != IMPORTS_TABLE_TYPE:
        if not is_success and is_retired(status, t):
            status_type = STATUS_DISABLED

    # Try to move work to available -- throw exception if not exist.
    # When it is going back to wait for its next check, that check lands
    # somewhere either side of the usual frequency (see SCHEDULE_JITTER).
    queue_t = None
    if status_type == STATUS_AVAILABLE:
        queue_t = jittered(t)

    mem_db.work[table_type][af].move_work(group_id, status_type, t=queue_t)

    # Update work with the new status.
    status.status = status_type

"""
The server uses this code to hand out jobs or work to the workers.
It works by traversing linked-lists of jobs.
The lists are ordered by oldest at the head / start, and
most recent items added at the end.

Efficient time-based checks can then occur across the entire list
since excluding earlier items based on being "too early" for re-scheduling
also implies that all items after it are also too early.

This is a simple task scheduler with Log(1) inserts and deletions
any where in the list (unlike regular Python data types.)
"""
def allocate_work(mem_db, need_afs, table_types, cur_time, mon_freq):
    # Get oldest work by table type and client AF preference.
    for table_choice in table_types:
        for need_af in need_afs:
            """
            The most recent items are always added at the end. Items at the start
            are oldest. If the oldest items are still too recent to pass time
            checks then we know that later items in the queue are also too recent.
            """
            wq = mem_db.work[table_choice][need_af]

            # Retired work is tried again after RETIRED_RECHECK, last of all.
            # Imports are left alone: disabled is where they go when done.
            queues = (STATUS_INIT, STATUS_AVAILABLE, STATUS_DEALT,)
            if table_choice != IMPORTS_TABLE_TYPE:
                queues += (STATUS_DISABLED,)

            for status_type in queues:
                for group_id, meta_group in wq.queues[status_type]:
                    group = meta_group.group

                    """
                    A server named by host name has no address until its
                    alias work resolves one. Handing it out before then gives
                    a worker nothing to check: at best the attempt is wasted,
                    and a TURN check given no address waits forever.

                    So it stays where it is -- skipped, not moved -- and goes
                    out on the first pass after /alias fills the address in.
                    Skipping never breaks the oldest-first ordering the
                    breaks below rely on; it only passes over one item.
                    """
                    if table_choice != ALIASES_TABLE_TYPE:
                        if any(not getattr(member, "ip", None) for member in group):
                            continue

                    # Never been allocated so safe to hand out.
                    if status_type == STATUS_INIT:
                        wq.move_work(group_id, STATUS_DEALT)
                        return list_x_to_dict(group)

                    # Work is moved back to available but don't do it too soon.
                    # Statuses are bulk updated for entries in a group.
                    work_timestamp = wq.timestamps[group_id]
                    elapsed = max(0, cur_time - work_timestamp)

                    # In time order with oldest first.
                    # So if this isn't old enough then none are.
                    if status_type == STATUS_AVAILABLE:
                        if elapsed < mon_freq:
                            break

                    # Retired: only once a week.
                    if status_type == STATUS_DISABLED:
                        if elapsed < RETIRED_RECHECK:
                            break

                    # Check for worker timeout.
                    if status_type == STATUS_DEALT:
                        if elapsed < WORKER_TIMEOUT:
                            break
                            
                    # Otherwise: allocate it as work.
                    wq.move_work(group_id, STATUS_DEALT)
                    return list_x_to_dict(group)
                
    return []

"""
The software supports doing DNS updates to aliases / FQNs.
If there's any services or imports that share that FQN then
this function is used to also update their IPs.
"""
def update_table_ip(mem_db, table_type: int, ip: str, alias_id: int, current_time: int):
    for record in mem_db.records_by_aliases[alias_id]:
        # Skip records that don't match the table type.
        if record.table_type != table_type:
            continue

        # 1) If current IP is invalid set new IP.
        status = mem_db.statuses[record.status_id]
        try:
            ensure_ip_is_public(record.ip)
        except:
            record.ip = ip
            continue

        # 2) If import and its never been checked set new IP.
        if table_type == IMPORTS_TABLE_TYPE:
            if not status.test_no:
                record.ip = ip
                continue

        # 3) Otherwise only update if there's a period of downtime.
        # This prevents servers from constantly changing IPs.
        cond_one = cond_two = False
        if not status.last_success and not status.last_uptime:
            if status.test_no >= 2:
                cond_one = True
        if status.last_success and status.last_uptime:
            elapsed = max(0, current_time - status.last_uptime)
            if elapsed > (MAX_SERVER_DOWNTIME * 2):
                cond_two = True

        # Only set ip if there's a period of downtime.
        if cond_one or cond_two:
            record.ip = ip

# A group's hostname comes off its first member. Written out because the
# three loops below used to read a leftover "entry" from the STUN loop above
# them, which put a STUN server's name on every MQTT and TURN entry.
def group_host(group):
    if not group:
        return None

    fqns = group[0].get("fqns")
    return fqns[0] if fqns else None

def gen_p2pd_legacy_settings(server_cache):
    map_servers = {
        "UDP": { "IPv4": [], "IPv6": [] },
        "TCP": { "IPv4": [], "IPv6": [] },
    }

    change_servers = {
        "UDP": { "IPv4": [], "IPv6": [] },
        "TCP": { "IPv4": [], "IPv6": [] },
    }

    mqtt_servers = {} # By id then coverted to list.

    turn_servers = {} # By id then coverted to list.

    # Build STUN "map" servers (RFC 5389)
    for af in server_cache["STUN(see_ip)"]:
        for proto in server_cache["STUN(see_ip)"][af]:
            for group in server_cache["STUN(see_ip)"][af][proto]:
                for entry in group:
                    if entry["fqns"]:
                        host = entry["fqns"][0]
                    else:
                        host = None

                    server = {
                        "mode": 2,
                        "host": host,
                        "primary": {
                            "ip": entry["ip"],
                            "port": entry["port"],
                        },
                        "secondary": {'ip': None, 'port': None}
                    }

                    map_servers[proto][af].append(server)

    # Build STUN change servers (RFC 3489)
    for af in server_cache["STUN(test_nat)"]:
        for proto in server_cache["STUN(test_nat)"][af]:
            for group in server_cache["STUN(test_nat)"][af][proto]:
                # RFC 3489 needs all four servers; a short group is unusable.
                if len(group) < 4:
                    continue

                host = group_host(group)
                server = {
                    "mode": 1,
                    "primary": {
                        "ip": group[0]["ip"],
                        "port": group[0]["port"],
                    },
                    "secondary": {
                        "ip": group[3]["ip"],
                        "port": group[3]["port"],
                    }
                }

                change_servers[proto][af].append(server)

    # Build MQTT server list.
    mqtt_servers = {}
    for af in server_cache["MQTT"]:
        for proto in server_cache["MQTT"][af]:
            for group in server_cache["MQTT"][af][proto]:
                host = group_host(group)
                rid = group[0]["id"]
                if rid not in mqtt_servers:
                    mqtt_servers[rid] = {}

                if "host" not in mqtt_servers[rid]:
                    mqtt_servers[rid]["host"] = host

                if not mqtt_servers[rid]["host"]:
                    mqtt_servers[rid]["host"] = host

                mqtt_servers[rid]["port"] = group[0]["port"]

                for k_af in ("IPv4", "IPv6"):
                    if k_af not in mqtt_servers[rid]:
                        mqtt_servers[rid][k_af] = None

                mqtt_servers[rid][af] = group[0]["ip"]

    mqtt_list = d_vals(mqtt_servers)

    # Build MQTT server list.
    turn_servers = {}
    for af in server_cache["TURN"]:
        for proto in server_cache["TURN"][af]:
            for group in server_cache["TURN"][af][proto]:
                host = group_host(group)
                rid = group[0]["id"]
                if rid not in turn_servers:
                    turn_servers[rid] = {}

                if "host" not in turn_servers[rid]:
                    turn_servers[rid]["host"] = host

                if not turn_servers[rid]["host"]:
                    turn_servers[rid]["host"] = host

                turn_servers[rid]["port"] = group[0]["port"]

                for k_af in ("IPv4", "IPv6"):
                    if k_af not in turn_servers[rid]:
                        turn_servers[rid][k_af] = None

 
                turn_servers[rid][af] = group[0]["ip"]
                if "afs" not in turn_servers[rid]:
                    turn_servers[rid]["afs"] = []

                turn_servers[rid]["afs"].append(af)
                turn_servers[rid]["user"] = group[0]["user"]
                turn_servers[rid]["pass"] = group[0]["password"]
                turn_servers[rid]["realm"] = None

    turn_list = d_vals(turn_servers)
    map_servers = map_servers
    change_servers = change_servers

    out = rf"""
STUN_MAP_SERVERS = {map_servers}

STUN_CHANGE_SERVERS = {change_servers}

MQTT_SERVERS = {mqtt_list}

TURN_SERVERS = {turn_list}
    """

    key_lookup = {
        "'UDP'": "UDP",
        "'TCP'": "TCP",
        "'IPv4'": "IP4",
        "'IPv6'": "IP6",
    }

    for k in key_lookup:
        out = out.replace(k, key_lookup[k])

    return out
