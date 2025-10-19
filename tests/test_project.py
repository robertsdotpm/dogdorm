import asyncio
import unittest
import aiosqlite
import copy
import subprocess
import uvicorn
from p2pd import *
from src import *

VALID_IMPORTS_TEST_DATA = [
    [None, STUN_MAP_TYPE, V4, "49.12.125.53", 3478, None, None],
    [None, NTP_TYPE, V4, "216.239.35.4", 123, None, None],
    [None, MQTT_TYPE, V4, "44.232.241.40", 1883, None, None],
    [None, TURN_TYPE, V4, "103.253.147.231", 3478, "quickblox", "baccb97ba2d92d71e26eb9886da5f1e0"],
]

####################################################################################
# groups .. group(s) ... fields inc list of fqns associated with it (maybe be blank)
# type * af * proto * group_len = ...
SERVICES_TEST_DATA = [
    [
        [
            [],
            STUN_CHANGE_TYPE, V4, UDP, "49.12.125.53", 3478
        ],
        [
            [],
            STUN_CHANGE_TYPE, V4, UDP, "49.12.125.53", 3479
        ],
        [
            [],
            STUN_CHANGE_TYPE, V4, UDP, "49.12.125.24", 3478
        ],
        [
            [],
            STUN_CHANGE_TYPE, V4, UDP, "49.12.125.24", 3479
        ],
    ],
    
    [
        [[], NTP_TYPE, V4, UDP, "216.239.35.4", 123],
    ]
    
]

IMPORTS_TEST_DATA = [
    [None, STUN_MAP_TYPE, V4, "49.12.125.53", 3478, None, None],
]

def insert_imports_test_data(mem_db, test_data):
    for info in test_data:
        fqn = info[0]
        info = info[1:]
        record = mem_db.insert_import(*info, fqn=fqn)

        # Set it up as work.
        mem_db.add_work(record["af"], IMPORTS_TABLE_TYPE, [record])

def insert_services_test_data(mem_db, test_data):
    for groups in test_data:
        records = []

        # All items in a group share the same group ID.
        for group in groups:

            # Store alias(es)
            alias = None
            try:
                for fqn in group[0]:
                    alias = mem_db.fetch_or_insert_alias(group[2], fqn)
                    break
            except:
                log_exception()

            alias_id = alias["id"] if alias else None
            record = mem_db.insert_service(
                service_type=group[1],
                af=group[2],
                proto=group[3],
                ip=ip_norm(group[4]),
                port=group[5],
                user=None,
                password=None,
                alias_id=alias_id
            )

            records.append(record)

        mem_db.add_work(records[0]["af"], SERVICES_TABLE_TYPE, records)

"""
While I update the list of servers, loading WAN addresses is going to be broken.
So I'll manually set this for both speed and reliability.
"""
if_info = {'id': 'eno1',
 'is_default': {2: True, 10: True},
 'mac': '00-1e-67-fa-5d-42',
 'name': 'eno1',
 'nat': {'delta': {'type': 1, 'value': 0},
         'delta_info': 'not applicable',
         'nat_info': 'open internet',
         'type': 1},
 'netiface_index': 1,
 'nic_no': 0,
 'rp': {2: [{'af': 2,
             'ext_ips': [{'af': 2, 'cidr': 32, 'ip': '158.69.27.176'}],
             'link_local_ips': [],
             'nic_ips': [{'af': 2, 'cidr': 32, 'ip': '158.69.27.176'}]}],
        10: [{'af': 10,
             'ext_ips': [{'af': 10, 'cidr': 128, 'ip': '2607:5300:60:80b0::1'}],
             'link_local_ips': [],
             'nic_ips': [{'af': 10, 'cidr': 128, 'ip': '2607:5300:60:80b0::1'}]}]
        }
}

async def run_cmd(*args):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()

class TestProject(unittest.IsolatedAsyncioTestCase):
    @classmethod
    async def init_interface(cls):
        cls.nic = Interface.from_dict(if_info)

    @classmethod
    def setUpClass(cls):
        # Run the async setup within the synchronous setUpClass
        asyncio.run(cls.init_interface())

    async def asyncSetUp(self):
        #db.setup_db()
        self.db = await aiosqlite.connect(DB_NAME)
        self.db.row_factory = aiosqlite.Row
        await delete_all_data(self.db)
        await init_settings_table(self.db)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_data_should_be_inserted(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        records = db.records[IMPORTS_TABLE_TYPE]
        for i in range(0, len(records)):
            assert(records[i]["ip"] ==  VALID_IMPORTS_TEST_DATA[i][3])


    async def test_import_complete_should_disable_status(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]
        row_id = work["id"]
        comp_status = {
            "is_success": 1,
            "status_id": work["status_id"],
            "t": int(time.time())
        }

        signal_complete_work(str([comp_status]))

        status = db.statuses[work["status_id"]]
        assert(status["status"] == STATUS_DISABLED)
        assert(work["id"] in db.records[IMPORTS_TABLE_TYPE])

    # TODO: check alias status and service status deleted for triggers.

    async def test_import_work_should_be_handed_out_once(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]
        for i in range(0, len(VALID_IMPORTS_TEST_DATA)):
            more_work = get_work()
            if more_work:
                more_work = more_work[0]
                print(work)
                print(more_work)
                assert(work["status_id"] != more_work["status_id"])

    async def test_alias_update_should_update_existing_ips(self):
        fqn = "x.com"
        dns_a = "9.9.9.9"
        fqn_test_data = VALID_IMPORTS_TEST_DATA[:]
        fqn_test_data = [[fqn] + td[1:] for td in fqn_test_data]
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        alias_id = db.fetch_or_insert_alias(int(IP4), fqn)["id"]
        for table_type in TABLE_TYPES:
            for row_id in db.records[table_type]:
                assert(db.records[table_type][row_id]["ip"] != dns_a)

        status_ids = [k for k in db.statuses]

        # Simulate 2 failed checks to ensure downtime > MAX_SERVER_DOWNTIME.
        for i in range(2, 4):
            for status_id in status_ids:
                outcome = {
                    "is_success": 0,
                    "status_id": int(status_id),
                    "t": int(time.time()) + i
                }

                signal_complete_work(str([outcome]))


        update_alias(alias_id, dns_a)
        for table_type in (ALIASES_TABLE_TYPE, SERVICES_TABLE_TYPE,):
            for row_id in db.records[table_type]:
                record = db.records[table_type][row_id]
                assert(record["ip"] == dns_a)
        

    async def test_insert_should_create_new_service(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]
        status_id = work["status_id"]


        service = {
            "service_type": work["type"],
            "af": int(work["af"]),
            "proto": int(UDP),
            "ip": work["ip"],
            "port": work["port"],
            "user": work["user"],
            "password": work["pass"],
            "alias_id": work["alias_id"]
        }

        assert(not len(db.records[SERVICES_TABLE_TYPE]))
        
        insert_services(str([[service]]), status_id)
        assert(len(db.records[SERVICES_TABLE_TYPE]))

    async def test_import_list_group_id_assumptions(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]
        status_id = work["status_id"]

        primary = {
            "service_type": STUN_CHANGE_TYPE,
            "af": int(IP4),
            "proto": int(UDP),
            "ip": "8.8.8.8",
            "port": 8000,
            "user": None,
            "password": None,
            "alias_id": None
        }

        secondary = copy.deepcopy(primary)
        secondary["ip"] = "8.8.4.4"

        another = copy.deepcopy(secondary)
        another["ip"] = "4.4.4.4"

        imports_list = [
            [primary, secondary],
            [another]
        ]

        insert_services(str(imports_list), status_id)

        rows = list(db.records[SERVICES_TABLE_TYPE].values())
        assert(rows[0]["group_id"] == rows[1]["group_id"])
        assert(rows[0]["group_id"] != rows[2]["group_id"])

    async def test_new_alias_should_be_allocatable_as_work(self):
        alias = db.fetch_or_insert_alias(int(IP4), "x.com")
        work = get_work()
        assert(len(work))

    async def test_work_reallocated_after_worker_timeout(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        for i in range(0, len(VALID_IMPORTS_TEST_DATA)):
            work = get_work()

        work = get_work()
        assert(not len(work))

        # Simulate fetch work after a long time.
        work = get_work(current_time=int(time.time()) + (WORKER_TIMEOUT * 2))
        assert(len(work))        

    async def test_work_not_allocated_before_second_threshold(self):
        test_data = SERVICES_TEST_DATA[:]
        for group in test_data:
            db.insert_services_test_data([group])
            work = get_work(monitor_frequency=10)
            assert(len(work))

            work_list = work[:]
            for serv in work_list:
                signal = {
                    "is_success": 1,
                    "status_id": serv["status_id"],
                    "t": int(time.time())
                }

                signal_complete_work(str([signal]))

            work = get_work()
            assert(not len(work))


            for serv in work_list:
                signal = {
                    "is_success": 1,
                    "status_id": serv["status_id"],
                    "t": int(time.time())
                }

                signal_complete_work(str([signal]))

            work = get_work()
            assert(not len(work))

            t = int(time.time()) + (MONITOR_FREQUENCY * 2)

            work = get_work(current_time=t)
            assert(len(work))

    async def test_allocated_work_should_be_marked_allocated(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()
        found = False
        for af in (IP4, IP6,):
            qs = db.work[IMPORTS_TABLE_TYPE][af].queues
            for allocated in qs[STATUS_DEALT]:
                if allocated[1]["group"] == work:
                    found = True

        assert(found)

    async def test_success_work_should_increase_uptime(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]
        service = {
            "service_type": work["type"],
            "af": work["af"],
            "proto": int(UDP),
            "ip": "8.8.8.8",
            "port": work["port"],
            "user": work["user"],
            "password": work["pass"],
            "alias_id": work["alias_id"]
        }


        insert_services(str([[service]]), work["status_id"])

        # Indicate complete for import work.
        work = {
            "is_success": 1,
            "status_id": work["status_id"],
            "t": int(time.time()) + 2
        }

        signal_complete_work(str([work]))

        # Indicate complete for service work.
        work = get_work()[0]
        work = {
            "is_success": 1,
            "status_id": work["status_id"],
            "t": int(time.time()) + 2
        }
        signal_complete_work(str([work]))
        work["t"] += 2
        signal_complete_work(str([work]))

        status = db.statuses[work["status_id"]]
        assert(status["uptime"])
        assert(status["uptime"] == status["max_uptime"])
        assert(status["test_no"] == 2)


    async def test_failed_work_should_reset_uptime(self):
        db.insert_services_test_data()
        work = get_work()[0]


        # First -- set uptime and max_uptime to a positive value.
        indicate_success = {
            "is_success": 1,
            "status_id": work["status_id"],
            "t": int(time.time()) + 2
        }

        signal_complete_work(str([indicate_success]))
        indicate_success["t"] += 2
        signal_complete_work(str([indicate_success]))


        # Then -- indicate a failed test.
        indicate_failure = {
            "is_success": 0,
            "status_id": work["status_id"],
            "t": int(time.time()) + 4
        }

        signal_complete_work(str([indicate_failure]))

        row = db.statuses[work["status_id"]]

        assert(not row["uptime"])
        assert(row["max_uptime"])
        assert(row["test_no"] == 3)

    async def test_monitor_stun_map_type(self):
        work = [{
            "af": IP4,
            "ip": "20.15.169.6", # Google
            "port": 3478,
            "proto": UDP,
            "status_id": None,
        }]

        import_list = await stun_server_classifier(
            af=IP4,
            ip="20.15.169.6",
            port=3478,
            nic=self.nic
        )

        print(import_list)
        return

        is_success = await monitor_stun_map_type(self.nic, work)
        print(is_success)
        return
        
        assert(is_success)

    async def test_monitor_stun_change_type(self):
        servers = [
            {"ip": "49.12.125.53", "port": 3478},
            {"ip": "49.12.125.53", "port": 3479},
            {"ip": "49.12.125.24", "port": 3478},
            {"ip": "49.12.125.24", "port": 3479},
        ]

        servers[0]["af"] = IP4
        servers[0]["proto"] = UDP
        for server in servers:
            server["status_id"] = None

        is_success = await monitor_stun_change_type(self.nic, servers)
        assert(is_success)

    async def test_monitor_mqtt_type(self):
        servers = [{
            "af": IP4,
            "proto": UDP,
            "ip": "44.232.241.40",
            "port": 1883,
            "status_id": None
        }]

        is_success = await monitor_mqtt_type(self.nic, servers)
        assert(is_success)


    async def test_monitor_turn_type(self):
        servers = [{
            "af": IP4,
            "proto": UDP,
            "ip": "103.253.147.231",
            "port": 3478,
            "status_id": None,
            "user": "quickblox",
            "pass": "baccb97ba2d92d71e26eb9886da5f1e0"
        }]

        is_success = await monitor_turn_type(self.nic, servers)
        assert(is_success)

    async def test_monitor_ntp_type(self):
        servers = [{
            "af": IP4,
            "proto": UDP,
            "ip": "216.239.35.4",
            "port": 123,
            "status_id": None
        }]

        is_success = await monitor_ntp_type(self.nic, servers)
        assert(is_success)

    async def test_alias_monitor(self):
        route = self.nic.route(IP4)
        curl = WebCurl(("23.220.75.245", 80,), route)
        alias = [{
            "fqn": "www.example.com",
            "af": IP4,
            "row_id": 0,
            "status_id": 0,
        }]

        is_success = await alias_monitor(curl, alias)
        assert(is_success)

    async def test_imports_monitor(self):
        route = self.nic.route(IP4)
        curl = WebCurl(("23.220.75.245", 80,), route)
        servers = []
        for info in VALID_IMPORTS_TEST_DATA:
            server = {
                "type": info[1],
                "af": info[2],
                "ip": info[3],
                "port": info[4],
                "user": info[5],
                "pass": info[6],
                "alias_id": 0,
                "status_id": 0,
            }

            await imports_monitor(curl, [server])


    async def test_worker_loop_exception_should_continue(self):
        is_success, _ = await worker(self.nic, None)
        assert(not is_success)

    async def test_status_should_be_created_on_new_alias(self):
        db.fetch_or_insert_alias(int(IP4), "x.com")
        assert(len(db.statuses))

    async def test_ipv6_works_at_all(self):
        test_data = [
            [
                None,
                STUN_MAP_TYPE, V6, "2a01:4f8:c17:8f74::1", 3478, None, None
            ],
        ]

        route = self.nic.route(IP4)
        curl = WebCurl(("example.com", 80,), route)
        servers = []
        for info in test_data:
            server = {
                "type": info[1],
                "af": info[2],
                "ip": info[3],
                "port": info[4],
                "user": info[5],
                "pass": info[6],
                "alias_id": 0,
                "status_id": 0,
            }

            assert(await imports_monitor(curl, [server]))

    async def test_insert_imports_with_invalid_data_should_fail(self):
        db.insert_imports_test_data(VALID_IMPORTS_TEST_DATA)
        work = get_work()[0]

        status_id = work["status_id"]
        service = {
            "service_type": work["type"],
            "af": work["af"],
            "proto": int(UDP),
            "ip": "8.8.8.8",
            "port": work["port"],
            "user": work["user"],
            "password": work["pass"],
            "alias_id": work["alias_id"]
        }

        try:
            bad_ip = copy.deepcopy(service)
            bad_ip["ip"] = "835sfasd"
            insert_services(str([[bad_ip]]), status_id)
            assert(0)
        except:
            pass

        

        try:
            bad_port = copy.deepcopy(service)
            bad_port["port"] = 5365452634
            insert_services(str([[bad_port]]), status_id)
            assert(0)
        except:
            pass


        
        try:
            bad_proto = copy.deepcopy(service)
            bad_proto["proto"] = 5365452634
            insert_services(str([[bad_proto]]), status_id)
            assert(0)
        except:
            pass

        
        try:
            bad_service_type = copy.deepcopy(service)
            bad_service_type["service_type"] = 4234
            insert_services(str([[bad_service_type]]), status_id)
            assert(0)
        except:
            pass

    

        try:
            bad_af = copy.deepcopy(service)
            bad_af["af"] = 1337
            insert_services(str([[bad_af]]), status_id)
            assert(0)
        except:
            pass

    async def test_service_deletion_should_remove_related_status(self):
        return # It doesn't since there's no trigger support.
        # TODO: would have to add this as cleanup to mem_schema.
        test_data = SERVICES_TEST_DATA[:]
        for group in test_data:
            db.insert_services_test_data(test_data=[group])

        work = get_work()
        sql = "DELETE FROM services WHERE id = ?"
        async with self.db.execute(sql, (work[0]["row_id"],)) as cursor:
            await self.db.commit()

        sql = "SELECT * FROM status WHERE id = ?"
        async with self.db.execute(sql, (work[0]["status_id"],)) as cursor:               
            rows = await cursor.fetchall()
            assert(not len(rows))   

    async def test_alias_created_for_import(self):
        fqn = "stun.gmx.de"
        db.insert_imports_test_data(test_data=[(
            fqn,
            STUN_MAP_TYPE,
            int(IP4),
            "212.227.67.33",
            3478,
        )])



        #print(db.work[IMPORTS_TABLE_TYPE][IP4].queues[STATUS_INIT].popleft())

        #return

        #assert(import_record)

        #print(import_record)

        assert(len(db.statuses) == 2)
        assert(len(db.records[ALIASES_TABLE_TYPE]) == 1)
        assert(len(db.records[IMPORTS_TABLE_TYPE]) == 1)



        # ^ should have two status rows.
        # Got to check its allocated as work.
        work_list = []
        work = get_work()
        #print(work)

 
        work_list.append(work[0])

        work = get_work()
        #print(work)

  
        work_list.append(work[0])


        # check worker process can handle alias work.
        alias_work = None
        import_work = None
        for work in work_list:
            
            if "fqn" in work:
                alias_work = work
            else:
                import_work = work
            


        # Not exactly the same as the process worker doing it with a web call.
        service = {
            "service_type": import_work["type"],
            "af": import_work["af"],
            "proto": int(UDP),
            "ip": import_work["ip"],
            "port": import_work["port"],
            "user": import_work["user"],
            "password": import_work["pass"],
            "alias_id": alias_work["id"]
        }

        insert_services(str([[service]]), import_work["status_id"])

        # check imports monitor can handle alias work.
        nic = await Interface()
        route = nic.route(IP4)
        curl = WebCurl(("8.8.8.8", 80,), route)
        is_success, status_ids = await worker(nic, curl, init_work=[alias_work])

        status_ids = [k for k in db.statuses]


        # Simulate 2 failed checks to ensure downtime > MAX_SERVER_DOWNTIME.
        for i in range(2, 4):
            for status_id in status_ids:
                outcome = {
                    "is_success": 0,
                    "status_id": int(status_id),
                    "t": int(time.time()) + i
                }

                signal_complete_work(str([outcome]))


        # Simulate an API update from a worker.
        sim_ip = "9.1.2.3"
        future_time = int(time.time()) + (MAX_SERVER_DOWNTIME * 3)
        out = update_alias(alias_work["id"], sim_ip, current_time=future_time)


        # Imports are only done once so they should not have changed.
        """
        sql = "SELECT * FROM imports"
        async with self.db.execute(sql) as cursor:
            rows = await cursor.fetchall()
            rows = [dict(row) for row in rows]
            assert(rows[0]["ip"] != sim_ip)
        """

        for serv_id in db.records[IMPORTS_TABLE_TYPE]:
            record = db.records[IMPORTS_TABLE_TYPE][serv_id]
            assert(record["ip"] != sim_ip)

        # Check services changes.
        for serv_id in db.records[SERVICES_TABLE_TYPE]:
            record = db.records[SERVICES_TABLE_TYPE][serv_id]
            assert(record["ip"] == sim_ip)


    async def test_monitor_turn_type_with_wrong_credentials(self):
        # Pass wrong user/pass to monitor_turn_type and assert failure
        pass

    async def test_alias_monitor_with_unresolvable_fqn_returns_status_ids(self):
        return
        
        # Pass an unresolvable FQN and assert failure
        route = self.nic.route(IP4)
        curl = WebCurl(("example.com", 80), route)
        ret = await alias_monitor(curl, alias)

    async def test_server_list_has_quality_score_set(self):
        pass

    # All work should end up being allocated, processed, then made available.
    # Then test that can be done multiple times.
    async def test_systemctl_cleans_out_work_queue_multiple_times(self):
        return
        # Run do imports.
        print("Importing all saved servers.")
        print("Importing done.")


        # maybe start server here first so server is in this proces and
        # doesnt start when systemctl runs but the workers do?
        # then you can call concurrency_test easily
        config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        #await server.serve()
        
        # Start systemctrl ...
        print("Starting monitoring system")
        result = subprocess.run(
            "sudo systemctl restart p2pd_monitor".split(" "),
            check=True,
            text=True,
            capture_output=True
        )
        
        await asyncio.sleep(5)

        await concurrency_test()


        
        # Stop systemctrl.
        print("stopping monitoring system")
        result = subprocess.run(
            "sudo systemctl stop p2pd_monitor".split(" "),
            check=True,
            text=True,
            capture_output=True
        )
        

    async def big_test(self):
        import_list = insert_main(db)
        #print(db.records)
        #print(len(db.records[IMPORTS_TABLE_TYPE]))

        # Ensure all records imported properly.
        for i in range(0, len(import_list)):
            out = find_record(
                db=db,
                ip=import_list[i]["ip"],
                port=import_list[i]["port"],
                fqn=import_list[i]["fqn"],
                import_type=import_list[i]["import_type"]
            )

            assert(out)

    async def some_test(self):
        file_content = """212.227.67.34,3478,stun.gmx.de
0,3478,stun.1cbit.ru
0,3478,stun.zepter.ru
0,3478,stun.voipgate.com
0,3478,stun.mixvoip.com
81.82.206.117,3478
80.156.214.187,3478
172.233.245.118,3478
193.22.17.97,3478
81.83.12.46,3478
90.145.158.66,3478
212.53.40.40,3478
81.3.27.44,3478
217.91.243.229,3478
212.144.246.197,3478
87.129.12.229,3478
69.20.59.115,3478
34.74.124.204,3478
91.212.41.85,3478
195.145.93.141,3478
95.216.145.84,3478
91.213.98.54,3478
212.53.40.43,3478
62.72.83.10,3478
80.155.54.123,3478
188.40.203.74,3478
129.153.212.128,3478
92.205.106.161,3478
0,3478,stun.peethultra.be
0,3478,stun.3deluxe.de
        """

        fqn_map = {
            "stun.1cbit.ru": "212.53.40.43",
            "stun.voipgate.com": "185.125.180.70",

            # Duplicate.
            "stun.zepter.ru": "212.53.40.43",
            "stun.mixvoip.com": "185.125.180.70",
            "stun.gmx.de": "212.227.67.34",

            # These two records conflict with existing import IPs.
            "stun.peethultra.be": "81.82.206.117",
            "stun.3deluxe.de": "217.91.243.229",
        }

        """
        Some servers may have fload prevention that drops queries to the same addr.
        """
        [[{'service_type': 3, 'af': 2, 'proto': 1, 'ip': '212.53.40.43', 'port': 3478, 'user': None, 'password': None, 'alias_id': 1, 'score': 0}]]
        [[{'service_type': 3, 'af': 2, 'proto': 1, 'ip': '212.53.40.43', 'port': 3478, 'user': None, 'password': None, 'alias_id': 2, 'score': 0}]]


        """
        Is it possible I introduced an edge case with duplicate names.
        """
        lines = file_content.splitlines()
        import_list = insert_from_lines(IP4, STUN_MAP_TYPE, lines, db)

        # Check pointers.
        for entry in import_list:
            found = False
            for group_id in db.groups:
                meta_group = db.groups[group_id]
                if id(meta_group.group[0]) == id(entry):
                    found = True

            assert(found)

        
        for fqn in fqn_map:
            alias = find_alias_by_fqn(db, fqn)
            assert(alias)
            msg = AliasUpdate(**{
                "t": int(time.time()),
                "alias_id": alias.id,
                "ip": fqn_map[fqn],
            })

            update_alias(msg)
            #print(alias)
        

        for record_id in db.records[IMPORTS_TABLE_TYPE]:
            record = db.records[IMPORTS_TABLE_TYPE][record_id]
            assert(record.ip not in ("", "0", None))



        """
        In previous releases these all worked so see if anything is missing
        as a warning -- it might be a sign that something broke.
        These IPs apparently cant be imported?
        """
        broken_stun = {'87.129.12.229', '92.205.106.161', '188.40.203.74', '81.83.12.46', '195.145.93.141', '212.53.40.43', '34.74.124.204', '95.216.145.84', '80.156.214.187', '81.3.27.44', '62.72.83.10', '217.91.243.229', '172.233.245.118', '69.20.59.115', '91.212.41.85', '80.155.54.123', '90.145.158.66', '212.53.40.40', '129.153.212.128', '91.213.98.54', '212.144.246.197', '193.22.17.97', '81.82.206.117'}


        work_req = WorkRequest(**{
            "stack_type": None,
            "table_type": IMPORTS_TABLE_TYPE,
            "current_time": None,
            "monitor_frequency": None
        })

        """
skipping duplicate
imports_list=[[Service(service_type=3, af=2, proto=1, ip='212.53.40.43', port=3478, user=None, password=None, alias_id=2, score=0)]] status_id=5
skipping duplicate
imports_list=[[Service(service_type=4, af=2, proto=2, ip='185.125.180.70', port=3478, user=None, password=None, alias_id=4, score=0), Service(service_type=4, af=2, proto=2, ip='185.125.180.70', port=3479, user=None, password=None, alias_id=4, score=0), Service(service_type=4, af=2, proto=2, ip='185.125.180.71', port=3478, user=None, password=None, alias_id=4, score=0), Service(service_type=4, af=2, proto=2, ip='185.125.180.71', port=3479, user=None, password=None, alias_id=4, score=0)], [Service(service_type=3, af=2, proto=1, ip='185.125.180.70', port=3478, user=None, password=None, alias_id=4, score=0)], [Service(service_type=3, af=2, proto=2, ip='185.125.180.70', port=3478, user=None, password=None, alias_id=4, score=0)]] status_id=9
skipping duplicate
imports_list=[[Service(service_type=3, af=2, proto=1, ip='212.53.40.43', port=3478, user=None, password=None, alias_id=None, score=0)]] status_id=27
skipping duplicate
imports_list=[[Service(service_type=3, af=2, proto=1, ip='81.82.206.117', port=3478, user=None, password=None, alias_id=5, score=0)], [Service(service_type=3, af=2, proto=2, ip='81.82.206.117', port=3478, user=None, password=None, alias_id=5, score=0)]] status_id=34
skipping duplicate
imports_list=[[Service(service_type=3, af=2, proto=1, ip='217.91.243.229', port=3478, user=None, password=None, alias_id=6, score=0)], [Service(service_type=3, af=2, proto=2, ip='217.91.243.229', port=3478, user=None, password=None, alias_id=6, score=0)]] status_id=36

        """
    
        while work := get_work(work_req):
            task = work[0]
            if task["table_type"] != IMPORTS_TABLE_TYPE:
                print("Got unwanted work type !", task["table_type"])
                continue

            out = await imports_monitor(self.nic, work)

            if not out:
                print(work)
                print("out empty skipping")

            req = InsertPayload(**{
                "imports_list": out,
                "status_id": int(work[0]["status_id"]),
            })

            try:
                insert_services(req)
            except DuplicateRecordError:
                print("skipping duplicate")
                print(req)

        for ip in broken_stun:
            found = False
            for record_id in db.records[SERVICES_TABLE_TYPE]:
                record = db.records[SERVICES_TABLE_TYPE][record_id]

                if ip == record.ip:
                    found = True
                    break

            if not found:
                print("Broken IP not inserted ", ip)

        server_list = build_server_list()
        for record_id in db.records[SERVICES_TABLE_TYPE]:
            record = db.records[SERVICES_TABLE_TYPE][record_id]

            found = False
            for service_type in ("STUN(see_ip)", "STUN(test_nat)",):
                for af in ("IPv4", "IPv6",):
                    for proto in ("UDP", "TCP",):
                        hey = server_list[service_type][af][proto]
                        for group in hey:
                            for entry in group:
                                if entry["ip"] == record.ip:
                                    found = True

            if not found:
                print(record)

        """
        If you have an IP entry imported and then have an entry with no IP
        but an FQN that points to has the same IP -- what happens?
        """

        """
        Are you having FQNs active for existing services? Test that
        And obviously since change has FQNs disabled the only way for that to
        happen is to re-run imports but restarting the server might be easier.
        """



def find_alias_by_fqn(db, fqn):
    for record_id in db.records[ALIASES_TABLE_TYPE]:
        record = db.records[ALIASES_TABLE_TYPE][record_id]
        if record.fqn == fqn:
            return record

def find_record(db, ip, port, fqn, import_type=STUN_MAP_TYPE, table_type=IMPORTS_TABLE_TYPE):
    for record_id in db.records[table_type]:
        record = db.records[table_type][record_id]
        if record["type"] != import_type:
            continue

        if ip != record["ip"]:
            continue

        if port != record["port"]:
            continue

        if record["alias_id"] is not None:
            alias = db.records[ALIASES_TABLE_TYPE][record["alias_id"]]
            if fqn != alias["fqn"]:
                continue


        return record