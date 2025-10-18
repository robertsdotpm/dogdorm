import asyncio
from fastapi import Depends
from ..defs import *
from .dealer_utils import *

@app.get("/list_groups")
async def api_list_groups():
    return mem_db.groups

@app.get("/list_records")
async def api_list_groups():
    return mem_db.records
    
@app.get("/concurrency_test", dependencies=[Depends(localhost_only)])
async def api_concurrency_test():
    # Wait for alias work to be done.
    print("Waiting for alias work to be done.")
    while 1:
        still_set = False
        for status_type in (STATUS_INIT, STATUS_AVAILABLE,):
            for af in VALID_AFS:
                q = mem_db.work[ALIASES_TABLE_TYPE][af].queues[status_type]
                if len(q):
                    still_set = True
                    print(af, " ", status_type, " ", len(q))

        if not still_set:
            break

        await asyncio.sleep(0.1)

    print("All aliases processed.")

@app.get("/list_aliases_len")
async def api_aliases_len():
    return len(mem_db.records[ALIASES_TABLE_TYPE])

@app.get("/list_aliases")
async def api_list_aliases():
    return mem_db.records[ALIASES_TABLE_TYPE]

@app.get("/sql_export", dependencies=[Depends(localhost_only)])
async def api_sql_export():
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        await sqlite_export(mem_db, sqlite_db)
        await sqlite_db.commit()
    return "done"

@app.get("/sql_import", dependencies=[Depends(localhost_only)])
async def api_sql_import():
    global server_cache
    await sqlite_import(mem_db)
    server_cache = build_server_list()
    return "done"

@app.get("/delete_all", dependencies=[Depends(localhost_only)])
async def api_delete_all():
    global mem_db
    mem_db = MemDB()
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        await delete_all_data(sqlite_db)
        await sqlite_db.commit()

    return "done"

@app.get("/insert_init", dependencies=[Depends(localhost_only)])
async def api_insert_init():
    global server_cache
    mem_db.setup_db()
    insert_main(mem_db)
    server_cache = build_server_list(mem_db)
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        try:
            await sqlite_db.execute("BEGIN")
            await delete_all_data(sqlite_db)
            await sqlite_export(mem_db, sqlite_db)
        except:
            log_exception()
            await sqlite_db.rollback()
            raise
        else:
            await sqlite_db.commit()

    return "done"