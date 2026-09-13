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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, PlainTextResponse, RedirectResponse
from p2pd import *
from typing import List
from pprint import pformat
from contextlib import asynccontextmanager
from email.utils import formatdate, parsedate_to_datetime
import hashlib
import json
from .dealer_defs import *
from .dealer_utils import *
from ..db.db_init import *
from ..txt_strs import *
from ..db.mem_db_utils import *
from ..db.mem_db import *

"""
Runs once on startup: the sqlite checkpoint is read back into the memory DB
and the CSV lists in server_lists are merged into it. On the way out the
memory DB is checkpointed again so a clean stop loses nothing.

(The names used here are defined further down the module; they are looked up
when this runs, not when it is defined.)
"""
@asynccontextmanager
async def lifespan(app: FastAPI):
    global refresh_task
    try:
        await sqlite_import(mem_db)

        # Merge CSV file imports with current mem DB.
        insert_main(mem_db)
    except Exception:
        log_exception()

    refresh_task = asyncio.create_task(refresh_server_cache())

    yield

    print("Server is stopping... cleaning up resources")

    # Stop the periodic checkpoint before taking the final one, so they
    # cannot both be writing to sqlite at once.
    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

    await save_all(mem_db)

app = FastAPI(default_response_class=PrettyJSONResponse, lifespan=lifespan)

# Allow any origin to fetch the JSON API from a browser (e.g. embedding
# /servers on a third-party site) without CORS errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)
# These handlers are deliberately async rather than plain def. FastAPI runs a
# non-async handler in a worker thread, which would let two of them mutate the
# memory DB at the same time -- and alongside the checkpoint task. Async means
# they run on the one event loop and, since none of them await, each runs to
# completion before the next starts. That is the atomicity mem_db.py assumes.
# /legacy stays sync on purpose: it rebuilds a large string and would block
# the loop, and it only reads.
mem_db = MemDB()
server_cache = {}
server_list_str = ""
refresh_task = None

# Validators for /servers, so a client that polls can be told "unchanged"
# instead of being sent the whole list again.
server_list_etag = ""
server_list_digest = ""
server_list_modified = 0

# Used to backup the memory-based database to sqlite.
async def save_all(mem_db):
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        try:
            await sqlite_db.execute("BEGIN")
            await delete_all_data(sqlite_db)
            await sqlite_export(mem_db, sqlite_db)
        except Exception:
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
    global server_list_etag
    global server_list_digest
    global server_list_modified
    while True:
        try:
            server_cache = build_server_list(mem_db)
            server_list_str = json.dumps(
                server_cache,
                indent=4,
                sort_keys=False,
                default=str
            )

            """
            The digest covers the servers, not the build time. The timestamp
            field changes every time this loop runs, so hashing the body as
            sent would hand out a new ETag every minute and no client would
            ever get a 304 -- which is the entire point of having one.
            """
            digest = hashlib.sha256(
                json.dumps(
                    {k: v for k, v in server_cache.items() if k != "timestamp"},
                    sort_keys=False,
                    default=str
                ).encode()
            ).hexdigest()[:32]

            if digest != server_list_digest:
                server_list_digest = digest
                server_list_etag = '"%s"' % (digest,)
                server_list_modified = int(time.time())

            await save_all(mem_db)
        except Exception:
            # Not a bare except: that also catches the CancelledError used
            # to stop this task at shutdown.
            log_exception()

        await asyncio.sleep(60)

# Since the API is mostly dynamic tell browsers not to cache it.
@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response: Response = await call_next(request)

    # A handler that set its own caching rules knows better than this does;
    # /servers wants to be revalidated, not refetched.
    if "Cache-Control" in response.headers:
        return response

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Hands out work (servers to check) to worker processes.
@app.post("/work", dependencies=[Depends(localhost_only)])
async def api_get_work(request: GetWorkReq):
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
async def api_work_done(payload: WorkDoneReq):
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
async def api_insert_services(payload: InsertServicesReq):
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
async def api_update_alias(data: AliasUpdateReq):
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

# Nothing here is meant to be read by a person, so send them to the
# dashboard that is. Temporary on purpose: a permanent redirect would be
# cached by browsers long after an operator changed ROOT_REDIRECT.
@app.get("/")
async def api_index():
    if ROOT_REDIRECT:
        return RedirectResponse(ROOT_REDIRECT, status_code=302)

    return PlainTextResponse("dogdorm dealer. The server list is at /servers\n")

"""
Show a listing of servers based on quality. The only public API is this one.

The list is most of a megabyte and only really changes when a check changes a
server's standing, which is hours apart -- so it is served with validators and
answers a conditional request with 304 instead of the body. A client that
sends no conditional header is unaffected and gets the full list as before.
"""
def not_modified(request: Request):
    if not server_list_etag:
        return False

    # An exact match is all we ever issue, but be tolerant of a list.
    inm = request.headers.get("if-none-match")
    if inm:
        return server_list_etag in [tag.strip() for tag in inm.split(",")]

    ims = request.headers.get("if-modified-since")
    if ims and server_list_modified:
        try:
            since = parsedate_to_datetime(ims).timestamp()
        except Exception:
            return False

        # Last-Modified only has second resolution, so this compares equal
        # for a client that echoes back exactly what we sent.
        return server_list_modified <= since

    return False

@app.get("/servers")
async def api_list_servers(request: Request):
    headers = {"Cache-Control": "public, max-age=0, must-revalidate"}
    if server_list_etag:
        headers["ETag"] = server_list_etag
    if server_list_modified:
        headers["Last-Modified"] = formatdate(server_list_modified, usegmt=True)

    if not_modified(request):
        return Response(status_code=304, headers=headers)

    return Response(
        content=server_list_str,
        media_type="application/json",
        headers=headers
    )

@app.get("/legacy", response_class=PlainTextResponse)
def api_list_p2pd_settings_legacy():
    return gen_p2pd_legacy_settings(server_cache)

# Support extended API methods for testing purposes.
if IS_DEBUG:
    cwd = get_script_parent()
    test_apis_path = os.path.join(cwd, "dealer_test_apis.py")
    exec(open(test_apis_path).read(), globals())

