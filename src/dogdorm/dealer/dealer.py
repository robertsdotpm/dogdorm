"""
The "dealer" is a server that hands out jobs / work items to workers.
It's implemented as a simple fastapi Python server.
All main functions are intentionally not async to help avoid locking issues.
Data is stored into a memory DB in db/mem_db. There is also a "background"
async task that periodically saves a backup of the memory database to an
sqlite DB.

When you restart the dealer -- it will consult the SQLite DB to
populate the initial mem DB with. A background task also updates what
the API returns every 1 minute. The reason this isn't live is the entire
DB has to be processed, scored, and turned to JSON, so for speed it gets
cached as a string ready to be returned instantly.
"""

import aiosqlite
from fastapi import FastAPI, Depends, Request
from fastapi.responses import Response
from p2pd import *
from typing import List
from pprint import pformat
import json
from .dealer_defs import *
from .dealer_utils import *
from ..db.db_init import *
from ..txt_strs import *
from ..db.mem_db_utils import *
from ..db.mem_db import *

app = FastAPI(default_response_class=PrettyJSONResponse)
mem_db = MemDB()
server_cache = []
server_list_str = ""
refresh_task = None

# Used to backup the memory-based database to sqlite.
async def save_all(mem_db):
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        try:
            await sqlite_db.execute("BEGIN")
            await delete_all_data(sqlite_db)
            await sqlite_export(mem_db, sqlite_db)
        except Exception:
            what_exception()
            log_exception()
            await sqlite_db.rollback()
            raise
        else:
            await sqlite_db.commit()

"""
Background task that periodically the list of monitored servers to return.
It also backs up the DB to disk. Async so hopefully doesn't block fastapi. 
"""
async def refresh_server_cache():
    global server_list_str
    global server_cache
    global mem_db
    while True:
        try:
            server_cache = build_server_list(mem_db)
            server_list_str = json.dumps(
                server_cache,
                indent=4,
                sort_keys=False,
                default=str
            )

            await save_all(mem_db)
        except:
            log_exception()
        await asyncio.sleep(60)

# Since the API is mostly dynamic tell browsers not to cache it.
@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

"""
This function is run once when the dealer is restarted.
It imports all the records from the sqlite DB into the memory DB.
It also merges the CSV fields in server_lists into the memory DB.
"""
@app.on_event("startup")
async def main():
    global refresh_task
    global mem_db
    try:
        await sqlite_import(mem_db)

        # Merge CSV file imports with current mem DB.
        insert_main(mem_db)
    except:
        log_exception()

    refresh_task = asyncio.create_task(refresh_server_cache())

"""
This hook is run when the server is given a kill signal.
The server tries to back up the memory DB to disk before exit so nothing
is lost. The restart.sh script also sends a request to do this before
it tries to aggressively kill the server.
"""
@app.on_event("shutdown")
async def shutdown_event():
    print("Server is stopping... cleaning up resources")
    await save_all(mem_db)

# Hands out work (servers to check) to worker processes.
@app.post("/work", dependencies=[Depends(localhost_only)])
def api_get_work(request: GetWorkReq):
    # Work can be selected based on type and even address family of server.
    stack_type = request.stack_type
    current_time = request.current_time or int(time.time())
    monitor_frequency = request.monitor_frequency or MONITOR_FREQUENCY
    table_type = request.table_type

    # Indicate IPv4 / 6 support of worker process.
    if stack_type == DUEL_STACK:
        need_afs = VALID_AFS
    else:
        need_afs = (stack_type,) if stack_type in VALID_AFS else VALID_AFS

    # Set table type.
    if table_type in TABLE_TYPES:
        table_types = (table_type,)
    else: 
        table_types = TABLE_TYPES

    # Allocate work from work queues based on req preferences.
    return allocate_work(
        mem_db,
        need_afs,
        table_types,
        current_time,
        monitor_frequency
    )

# Indicate that work has been completed.
@app.post("/complete", dependencies=[Depends(localhost_only)])
def api_work_done(payload: WorkDoneReq):
    results: List[int] = []
    for status_info in payload.statuses:
        try:
            ret = mark_complete(mem_db, **status_info.dict())
            results.append(ret)
        except KeyError:
            log_exception()
            continue

    return results

"""
# This is a special method called only for complete import work
# that resulted in a valid online server and indicates to the dealer
# to start monitoring that new service.
"""
@app.post("/insert", dependencies=[Depends(localhost_only)])
def api_insert_services(payload: InsertServicesReq):
    # One import can result in learning multiple groups of
    # related servers to start monitoring.
    for groups in payload.imports_list:
        # If any servers in a group already exist then skip add.
        try:
            records = []
            alias_count = 0
            for service in groups:
                # Convert Pydantic model to dict
                record = mem_db.insert_service(**service.dict())
                records.append(record)

                if service.alias_id is not None:
                    alias_count += 1

            # STUN change servers should have all or no alias.
            if records[0].type == STUN_CHANGE_TYPE:
                if alias_count not in (0, 4):
                    # TODO: delete created records
                    raise Exception("STUN change servers need even aliases")

            mem_db.add_work(records[0].af, SERVICES_TABLE_TYPE, records)
        except DuplicateRecordError:
            log_exception()
            continue

    # Only allocate imports work once.
    # This deletes the associated status record. 
    mark_complete(
        mem_db,
        1 if len(payload.imports_list) else 0,
        payload.status_id
    )

    return []

# Special method only called by alias work to update DNS IPs.
@app.post("/alias", dependencies=[Depends(localhost_only)])
def api_update_alias(data: AliasUpdateReq):
    # Only want public IPs.
    ip = ensure_ip_is_public(data.ip)
    current_time = data.current_time or int(time.time())
    alias_id = data.alias_id
    if alias_id not in mem_db.records[ALIASES_TABLE_TYPE]:
        raise Exception("Alias id not found.")
    
    # Load the alias record to update.
    alias = mem_db.records[ALIASES_TABLE_TYPE][alias_id]

    # Update alias by IP mappings.
    mem_db.del_alias_by_ip(alias)
    alias.ip = ip
    mem_db.add_alias_by_ip(alias)

    # Any record that uses the alias also has its IP updated.
    for table_type in (IMPORTS_TABLE_TYPE, SERVICES_TABLE_TYPE):
        update_table_ip(mem_db, table_type, ip, alias_id, current_time)

    return []

# Show a listing of servers based on quality
# Only public API is this one.
@app.get("/servers")
async def api_list_servers():
    return Response(content=server_list_str, media_type="application/json")

# Support extended API methods for testing purposes.
if IS_DEBUG:
    cwd = get_script_parent()
    test_apis_path = os.path.join(cwd, "dealer_test_apis.py")
    exec(open(test_apis_path).read(), globals())

