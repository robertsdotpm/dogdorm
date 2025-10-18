import time
from typing import Any
from collections import OrderedDict
from ..defs import *
from ..worker.work_queue import *
from .mem_db_defs import *
from p2pd import *

class MemDB():
    def __init__(self):
        self.setup_db() 

    def setup_db(self):
        self.statuses = {} # id: status
        self.groups = {} # group_id: [status ...]
        self.work = {} # [table_type] -> queue -> [status ...]
        for table_type in TABLE_TYPES:
            self.work[table_type] = {}
            for af in [IP4, IP6]:
                self.work[table_type][int(af)] = WorkQueue()

        self.records = {} # [table_type][id] => record
        self.id_max = {}
        for table_type in TABLE_TYPES:
            self.records[table_type] = {}
            self.id_max[table_type] = 0

        self.id_max[GROUPS_TABLE_TYPE] = 0
        self.id_max[STATUS_TABLE_TYPE] = 0
        self.records_by_aliases = {}
        self.aliases_by_ip = {}

        # Unique indexes.
        self.uniques = {
            ALIASES_TABLE_TYPE: UniqueIndex(["af", "fqn"]),
            SERVICES_TABLE_TYPE: UniqueIndex([
                "type",
                "af",
                "proto",
                "alias_id_or_ip",
                "port"
            ]),
            IMPORTS_TABLE_TYPE: UniqueIndex([
                "type",
                "af",
                "proto",
                "alias_id_or_ip",
                "port"
            ])
        }

        # Table name mappings.
        self.tables = OrderedDict({
            ALIASES_TABLE_TYPE: self.records[ALIASES_TABLE_TYPE],
            IMPORTS_TABLE_TYPE: self.records[IMPORTS_TABLE_TYPE],
            SERVICES_TABLE_TYPE: self.records[SERVICES_TABLE_TYPE],
            STATUS_TABLE_TYPE: self.statuses
        })

    def add_id(self, table_type, n):
        if self.id_max[table_type] < n:
            self.id_max[table_type] = n

    def get_id(self, table_type):
        self.id_max[table_type] += 1
        return self.id_max[table_type]

    def add_alias_by_ip(self, alias):
        if alias.ip is None:
            return
        
        if alias.ip not in self.aliases_by_ip:
            self.aliases_by_ip[alias.ip] = []

        if alias not in self.aliases_by_ip[alias.ip]:
            self.aliases_by_ip[alias.ip].append(alias)

    def del_alias_by_ip(self, alias):
        if alias.ip is None:
            return
        
        if alias.ip not in self.aliases_by_ip:
            return
        
        if alias in self.aliases_by_ip[alias.ip]:
            self.aliases_by_ip[alias.ip].remove(alias)

    def add_work(self, af: int, table_type: int, group: Any, group_id=None, status_type=STATUS_INIT):
        # Save this as a new "group".
        group_id = group_id or self.get_id(GROUPS_TABLE_TYPE)
        meta_group = MetaGroup(**{
            "id": group_id,
            "group": group,
            "table_type": table_type,
            "af": af
        })
        self.groups[group_id] = meta_group

        # Add group to work queue LOG(1).
        self.work[table_type][af].add_work(group_id, meta_group, status_type)

        # Add group id field.
        for member in group:
            member.group_id = group_id

        return meta_group

    def init_status_row(self, row_id: int, table_type: int):
        # Associated row must exist.
        if row_id not in self.records[table_type]:
            raise KeyError(f"{row_id} not in records {table_type}")
        
        status_id = self.get_id(STATUS_TABLE_TYPE)
        status = StatusType(**{
            "id": status_id,
            "row_id": row_id,
            "table_type": table_type,
            "status": STATUS_INIT,
            "last_status": int(time.time()),
            "test_no": 0,
            "failed_tests": 0,
            "last_success": 0,
            "last_uptime": 0,
            "uptime": 0,
            "max_uptime": 0
        })

        self.statuses[status_id] = status
        return status

    def record_alias(self, af: int, fqn: str, ip=None):
        alias_id = self.get_id(ALIASES_TABLE_TYPE)
        alias = AliasType(**{
            "id": alias_id,
            "af": af,
            "fqn": fqn,
            "ip": ip,
            "group_id": None,
            "status_id": None,
            "table_type": ALIASES_TABLE_TYPE
        })

        # Check unique constraint.
        self.uniques[ALIASES_TABLE_TYPE].add(alias)

        # Record the new alias.
        self.records[ALIASES_TABLE_TYPE][alias_id] = alias
        self.records_by_aliases[alias_id] = []

        # Record the IP.
        if ip is not None:
            self.add_alias_by_ip(alias)

        # Create a new status entry for this.
        status = self.init_status_row(alias_id, ALIASES_TABLE_TYPE)
        alias.status_id = status.id

        # Set it up as work.
        self.add_work(af, ALIASES_TABLE_TYPE, [alias])
        return alias

    def fetch_or_insert_alias(self, af: int, fqn: str, ip=None):
        alias = self.uniques[ALIASES_TABLE_TYPE].get_key((af, fqn))
        if alias:
            return alias
        
        return self.record_alias(af, fqn, ip=ip)

    def insert_record(self, table_type: int, record_type: int, af: int, ip: Any, port: int, user: Any, password: Any, proto=None, fqn=None, alias_id=None, score=0):
        # Some servers like to point to local resources for trickery.
        if ip not in ("0", "", None):
            ensure_ip_is_public(ip)
        else:
            ip = None
            if fqn is None:
                raise ValueError("No way to resolve this IP for insert record.")

        # Load alias row to ensure it exists.
        if alias_id is not None:
            if alias_id not in self.records[ALIASES_TABLE_TYPE]:
                raise KeyError("No alias called id " + str(alias_id))
            else:
                # Disable aliases for STUN change servers.
                if record_type == STUN_CHANGE_TYPE:
                    alias_id = None

        # Get imports id record.
        row_id = self.get_id(table_type)

        # Record imports record.
        record = RecordType(**{
            "id": row_id,
            "table_type": table_type,
            "type": record_type,
            "af": af,
            "proto": proto,
            "ip": ip,
            "port": port,
            "user": user,
            "password": password, # pass
            "alias_id": alias_id,
            "status_id": None,
            "group_id": None,
            "score": score
        })

        # Select the correct UniqueIndex
        if table_type == SERVICES_TABLE_TYPE:
            unique_index = self.uniques[SERVICES_TABLE_TYPE]
        else:
            unique_index = self.uniques[IMPORTS_TABLE_TYPE]

        # Add record to the index (raises KeyError on duplicate)
        try:
            unique_index.add(record)
        except KeyError:
            raise DuplicateRecordError(
                f"Row already exists: type={record.type}, af={record.af}"
            )

        # Save in services table.
        self.records[table_type][row_id] = record

        # Init status row.
        status = self.init_status_row(row_id, table_type)
        record.status_id = status.id

        # Look this up by alias_id.
        if alias_id is not None:
            self.records_by_aliases[alias_id].append(record)

        return record

    def insert_import(self, import_type: int, af: int, ip: Any, port: int, user=None, password=None, fqn=None, score=0):
        # Create alias record.
        if fqn:
            alias = self.fetch_or_insert_alias(af, fqn)
            alias_id = alias.id
        else:
            alias_id = None

        return self.insert_record(
            table_type=IMPORTS_TABLE_TYPE,
            record_type=import_type,
            af=af,
            ip=ip,
            port=port,
            user=user,
            password=password,
            alias_id=alias_id,
            fqn=fqn,
            score=score
        )

    def insert_service(self, service_type: int, af: int, proto: int, ip: Any, port: int, user: Any, password: Any, alias_id: Any, score=0):
        return self.insert_record(
            table_type=SERVICES_TABLE_TYPE,
            record_type=service_type,
            af=af,
            ip=ip,
            port=port,
            user=user,
            password=password,
            proto=proto,
            alias_id=alias_id,
            score=score
        )
