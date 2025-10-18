from p2pd import *
from ..defs import *
from ..dealer.dealer_utils import *

IMPORT_ROOT = os.path.join(get_script_parent(), "..", "..", "..", "server_lists")
IMPORT_FILES = (
    "stun_v4.csv",
    "stun_v6.csv",
    "mqtt_v4.csv",
    "mqtt_v6.csv",
    "turn_v4.csv",
    "turn_v6.csv",
    "ntp_v4.csv",
    "ntp_v6.csv"
)

def insert_from_lines(af, import_type, lines, db):
    import_list = []
    for line in lines:
        try:
            line = line.strip()
            parts = line.split(",")
            ip = None if parts[0] in ("0", "") else parts[0]
            port = parts[1]
            user = password = fqn = None
            if len(parts) > 2:
                fqn = parts[2]
            if len(parts) > 3:
                user = parts[3]
            if len(parts) > 4:
                password = parts[4]

            import_record = {
                "import_type": import_type,
                "af": int(af),
                "ip": ip,
                "port": int(port),
                "user": user,
                "password": password,
                "fqn": fqn
            }

            record = db.insert_import(**import_record)
            import_list.append(record)
            db.add_work(af, IMPORTS_TABLE_TYPE, [record])
        except DuplicateRecordError: # ignore really.
            log_exception()
        except:
            what_exception()

    return import_list

def insert_main(db):
    import_list = []
    for import_file in IMPORT_FILES:
        af = IP4 if "v4" in import_file else IP6
        import_type = None
        for service_name in SERVICE_LOOKUP:
            if service_name in import_file:
                import_type = SERVICE_LOOKUP[service_name]
                break

        if not import_type:
            print("Could not determine import type for file: ", import_file)
            break
            
        file_path = os.path.join(IMPORT_ROOT, import_file)
        if not os.path.exists(file_path):
            print("Could not find file: ", file_path)
            continue


        with open(file_path, "r") as f:
            lines = f.readlines()
            import_list += insert_from_lines(af, import_type, lines, db)

    return import_list

async def delete_all_data(sqlite_db):
    for table in ("settings", "services", "aliases", "status", "imports"):
        sql = "DELETE FROM %s;" % (table)
        await sqlite_db.execute(sql)

async def init_settings_table(sqlite_db):
    sql = "INSERT INTO settings (key, value) VALUES (?, ?)"
    params = ("max_server_downtime", MAX_SERVER_DOWNTIME,)
    await sqlite_db.execute(sql, params)
    await sqlite_db.commit()

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