import asyncio
import unittest
import aiosqlite
import copy
import subprocess
from p2pd import *
from src import *

class TestNew(unittest.IsolatedAsyncioTestCase):
    @classmethod
    async def init_interface(cls):
        #cls.nic = await Interface()
        pass


    @classmethod
    def setUpClass(cls):
        # Run the async setup within the synchronous setUpClass
        asyncio.run(cls.init_interface())

    async def asyncSetUp(self):
        pass

    async def asyncTearDown(self):
        pass

    async def test_main(self):
        global db
        status = db.init_status_row(5, IMPORTS_TABLE_TYPE)
        status = db.init_status_row(6, IMPORTS_TABLE_TYPE)
        print(db.statuses)
        print()

        alias = db.record_alias(V4, "google.com", "8.8.8.8")
        print(alias)
        print()
        alias = db.fetch_or_insert_alias(V4, "google.com")
        print(alias)
        print()

        db.records[ALIASES_TABLE_TYPE]
        print(db.records[ALIASES_TABLE_TYPE])
        print()

        import_record = db.insert_import(STUN_CHANGE_TYPE, V4, "8.8.8.8", 4444, fqn="google.com")
        print(import_record)

        print()
        print("Insert service record")
        group_id = 2
        db.insert_service(STUN_CHANGE_TYPE, IP4, UDP, "8.8.8.8", 3333, None, None, alias["id"])

        print(db.records[SERVICES_TABLE_TYPE])

        work = get_work()
        print("Got work = ")
        print(work)

  
        t = int(time.time()) + 2
        status_id = work[0]["status_id"]
        db.mark_complete(1, status_id, t)

        print(db.statuses[status_id])

        t = t + 2
        db.mark_complete(1, status_id, t)
        print(db.statuses[status_id])

        t = t + 2
        db.mark_complete(0, status_id, t)
        print(db.statuses[status_id])