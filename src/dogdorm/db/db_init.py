"""
The dealer server can popular its memory database with entries stored
in CSV text files. That is how those servers are imported for monitoring.
The memory DB is periodically backed up to an sqlite database. When
the server starts this sqlite database is imported back to memory and
the CSV files are merged into memory with uniqueness checks.
"""

from p2pd import *
from ..defs import *
from ..dealer.dealer_utils import *

# Get abs path to this file then work out where the CSV files are.
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

SERVICE_LOOKUP = {
    "stun": STUN_MAP_TYPE,
    "mqtt": MQTT_TYPE,
    "turn": TURN_TYPE,
    "ntp": NTP_TYPE,
}

# For each row in the CSV, try to import it into memory.
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
            #log_exception()
            pass
        except:
            what_exception()

    return import_list

# For every file break it into lines.
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

# Clear all records in sqlite backup DB.
async def delete_all_data(sqlite_db):
    for table in ("settings", "services", "aliases", "status", "imports"):
        sql = "DELETE FROM %s;" % (table)
        await sqlite_db.execute(sql)

