"""
The worker is a Python 3 process that gets work from the dealer server.
The work consists of monitoring for DNS changes, checking possible imports,
and assessing whether servers are online or not.
"""

import asyncio
import random
from p2pd import *
from ..defs import *
from .worker_utils import *
from .worker_monitors import *
from ..txt_strs import *

async def worker(nic, curl, init_work=None, table_type=None):
    status_ids = []
    try:
        # A single group of work, 1 or more grouped long.
        work = init_work or (await fetch_work_list(curl, table_type))
        if work == INVALID_SERVER_RESPONSE:
            print("Invalid server response, try again.")
            return 0, []
        if not len(work):
            print("No work found")
            return NO_WORK, []

        print("got work = ", work)

        is_success = 0
        status_ids = [w["status_id"] for w in work if "status_id" in w]
        table_type = work[0]["table_type"]

        print()
        proto = "ANY"
        if "proto" in work[0]:
            if work[0]["proto"]:
                proto = TXTS["proto"][work[0]["proto"]]
            
        print("Doing %s work for %s on %s:%s:%d" % (
            TXTS[table_type],
            TXTS[work[0]["type"]] if "type" in work[0] else work[0]["fqn"],
            proto,
            work[0]["ip"],
            int(work[0]["port"] if "port" in work[0] else "53"),
        ))

        if table_type == IMPORTS_TABLE_TYPE:
            imports_list = await imports_monitor(nic, work)

            # Otherwise do imports.
            if imports_list:
                params = {
                    "imports_list": imports_list,
                    "status_id": int(work[0]["status_id"]),
                }
                await retry_curl_on_locked(curl, params, "/insert")

                print("Found -- importing new servers")
            else:
                print("Not importing.")

        if table_type == SERVICES_TABLE_TYPE:
            is_success = await service_monitor(nic, work)
            if is_success:
                print("Online -- updating uptime", status_ids)
            else:
                print("Offline -- updating uptime", status_ids)
        
        
        if table_type == ALIASES_TABLE_TYPE:
            res_ip = await asyncio.wait_for(
                alias_monitor(curl, work),
                2
            )

            if res_ip:
                params = {"alias_id": int(work[0]["id"]), "ip": res_ip}
                await retry_curl_on_locked(curl, params, "/alias")
                print("Resolved -- updating IPs", status_ids)
            else:
                print("No IP found for DNS -- not updating ", status_ids)
        

        print("Work status updated.")
        return 1, status_ids
    except:
        what_exception()
        log_exception()
        return 0, status_ids

async def process_work(nic, curl, table_type=None, stagger=False):
    await sleep_random(100, 4000)

    # Execute work from the dealer server.
    is_success, status_ids = await worker(nic, curl, table_type=table_type)
    if is_success == NO_WORK:
        # Between 1 - 5 mins.
        await sleep_random(60000, 300000)
        return

    # Update statuses.
    await async_wrap_errors(
        update_work_status(curl, status_ids, is_success)
    )

async def main(nic=None):
    print("Loading interface...")
    nic = nic or Interface.from_dict(IF_INFO)
    print("Interface loaded: ", nic)

    # Workers start randomly over the next min to avoid traffic surges.
    #await sleep_random(1000, 60000)

    endpoint = ("127.0.0.1", 8000,)
    route = nic.route(IP4)
    curl = WebCurl(endpoint, route)

    """
    If there's many items in a work queue then the workers might never get
    to the end of it before moving to the next queue. So the queue to process
    is chosen randomly with a bias towards services.
    """
    tables = (SERVICES_TABLE_TYPE, IMPORTS_TABLE_TYPE, ALIASES_TABLE_TYPE,)
    table = random.choice(tables)
    while 1:
        start_time = time.perf_counter()
        await async_wrap_errors(
            process_work(nic, curl, table_type=table)
        )

        exec_elapsed = time.perf_counter() - start_time
        if exec_elapsed <= 0.5:
            ms = int(exec_elapsed * 1000)
            await sleep_random(max(100, 500 - ms), 1000)

    # Give time for event loop to finish.
    await asyncio.sleep(2)
        
"""
maybe the workers were running out of sockets due to those old bugs.
Or some other error from long running processes. They could periodically
restart every day of uptime for a worker so they get a clean slate.

need to add timeouts to worker stuff
"""