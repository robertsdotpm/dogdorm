import math
import time
import json
from fastapi.responses import JSONResponse
from fastapi import Request, HTTPException
from p2pd import *
from ..defs import *
from ..txt_strs import *
from ..db.db_init import *


def localhost_only(request: Request):
    client_host = request.client.host
    if client_host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Access forbidden")

class PrettyJSONResponse(JSONResponse):
    def render(self, content: any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,        # pretty-print here
        ).encode("utf-8")

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

def get_fqn_list(mem_db, ip):
    if ip is None:
        return []
    
    fqns = set()
    if ip in mem_db.aliases_by_ip:
        for alias in mem_db.aliases_by_ip[ip]:
            if alias.fqn is not None:
                fqns.add(alias.fqn)

    return list(fqns)[::-1]

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
            meta_group = mem_db.groups[group_id]
            if meta_group.table_type != SERVICES_TABLE_TYPE:
                continue

            scores = []
            fields = ("test_no", "failed_tests", "uptime", "max_uptime", "last_success")
            group = list_x_to_dict(meta_group.group)
            for record in group:
                try:
                    status_obj = mem_db.statuses.get(record.get("status_id"))
                    if not status_obj:
                        continue
                    status = getattr(status_obj, "dict", lambda: {})()


                    for k in fields:
                        record[k] = status.get(k, 0)

                    record["score"] = compute_service_score(status)
                    record["fqns"] = get_fqn_list(mem_db, record.get("ip"))
                    scores.append(record["score"])
                except Exception:
                    # Skip invalid record but continue processing others
                    continue

            # Compute average score if any
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
                try:
                    by_proto = s[TXTS[service_type]][TXTS["af"][af]][TXTS["proto"][proto]]
                    by_proto.sort(key=lambda x: x[0].get("score", 0), reverse=True)
                except Exception:
                    continue

    s["timestamp"] = int(time.time())
    return s

def mark_complete(mem_db, is_success: int, status_id: int, t=None):
    t = t or int(time.time())
    status_type = STATUS_AVAILABLE
    if status_id not in mem_db.statuses:
        raise KeyError("could not load status row %s" % (status_id,))
    
    # Delete target row if status is for an imports.
    # We only want imports work to be done once.
    status = mem_db.statuses[status_id]
    table_type = status.table_type
    if table_type == IMPORTS_TABLE_TYPE:
        if status.test_no >= IMPORT_TEST_NO:
            status_type = STATUS_DISABLED
        if is_success:
            status_type = STATUS_DISABLED

    # Remove from dealt queue.
    record = mem_db.records[table_type][status.row_id]
    af = record.af
    group_id = record.group_id

    # Try to move work to available -- throw exception if not exist.
    mem_db.work[table_type][af].move_work(group_id, status_type)

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
    
    status.status = status_type
    status.test_no += 1
    status.last_status = t

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
            for status_type in (STATUS_INIT, STATUS_AVAILABLE, STATUS_DEALT,):
                for group_id, meta_group in wq.queues[status_type]:
                    group = meta_group.group

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
                    if status_type != STATUS_DEALT:
                        if elapsed < mon_freq:
                            break

                    # Check for worker timeout.sd
                    # TODO: this line is making work get reallocated.
                    if status_type == STATUS_DEALT:
                        if elapsed < WORKER_TIMEOUT:
                            break
                            
                    # Otherwise: allocate it as work.
                    wq.move_work(group_id, STATUS_DEALT)
                    return list_x_to_dict(group)
                
    return []

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