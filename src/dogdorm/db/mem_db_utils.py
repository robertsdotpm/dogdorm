from dataclasses import asdict, fields, is_dataclass
from collections import OrderedDict
import aiosqlite
import sqlite3
from ..defs import *
from ..worker.work_queue import *
from .mem_db_defs import *
from p2pd import *

async def insert_object(db, table, obj):
    # get existing columns
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = {row[1] async for row in cursor}  

    data = asdict(obj) if hasattr(obj, "__dataclass_fields__") else vars(obj)
    valid = {k: v for k, v in data.items() if k in columns}

    if not valid:
        return

    cols = ", ".join(valid.keys())
    placeholders = ", ".join("?" for _ in valid)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    await db.execute(sql, tuple(valid.values()))

async def load_objects(db, table, cls, where_clause: str = None, where_args: tuple = ()):
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        db_cols = {row[1] async for row in cursor}

    # Get class fields dynamically
    if is_dataclass(cls):
        class_fields = [f.name for f in fields(cls)]
    elif hasattr(cls, "model_fields"):  # Pydantic v2
        class_fields = list(cls.model_fields.keys())
    elif hasattr(cls, "__fields__"):  # Pydantic v1
        class_fields = list(cls.__fields__.keys())
    else:
        raise TypeError(f"Cannot introspect fields of {cls}")

    select_cols = [c for c in class_fields if c in db_cols]
    if not select_cols:
        return []

    sql = f"SELECT {', '.join(select_cols)} FROM {table}"
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += " ORDER BY id ASC"

    async with db.execute(sql, where_args) as cursor:
        rows = await cursor.fetchall()
        col_index = {desc[0]: i for i, desc in enumerate(cursor.description)}

    objs = []
    for row in rows:
        kwargs = {col: row[col_index[col]] for col in select_cols}
        objs.append(cls(**kwargs))
    return objs

async def sqlite_export(mem_db, sqlite_db):
    for table_type in mem_db.tables:
        for record_id in mem_db.tables[table_type]:
            entry = mem_db.tables[table_type][record_id]
            table_name = MEM_DB_ENUMS[table_type]
            try:
                await insert_object(sqlite_db, table_name, entry)
            except sqlite3.IntegrityError as e:
                what_exception()
                continue
            except:
                log_exception()

async def sqlite_import(mem_db):
    async with aiosqlite.connect(DB_NAME) as sqlite_db:
        # 1. Load all StatusType rows in batch
        all_statuses = await load_objects(sqlite_db, "status", StatusType)
        for status in all_statuses:
            mem_db.add_id(STATUS_TABLE_TYPE, status.id + 1)
            mem_db.statuses[status.id] = status

        # Used to rebuild groups table.
        group_maps = OrderedDict({
            ALIASES_TABLE_TYPE: {},
            IMPORTS_TABLE_TYPE: {},
            SERVICES_TABLE_TYPE: {}
        })

        # 2. Load main tables.
        for table_type in group_maps:
            cls = MEM_DB_TYPES[table_type]
            table_name = MEM_DB_ENUMS[table_type]
            objs = await load_objects(sqlite_db, table_name, cls)
            for obj in objs:
                # Insert into main table dict
                mem_db.add_id(table_type, obj.id + 1)
                mem_db.tables[table_type][obj.id] = obj

                # Try increase max group_id.
                mem_db.add_id(GROUPS_TABLE_TYPE, obj.group_id + 1)
                if obj.group_id not in group_maps[table_type]:
                    group_maps[table_type][obj.group_id] = []
                group_maps[table_type][obj.group_id].append(obj)

                # Rebuild unique indexes
                mem_db.uniques[table_type].add(obj)
                if table_name == "aliases":
                    mem_db.records_by_aliases[obj.id] = []
                    mem_db.add_alias_by_ip(obj)
                else:
                    if obj.alias_id is not None:
                        mem_db.records_by_aliases[obj.alias_id].append(obj)

    # After loading all tables
    for status in mem_db.statuses.values():
        table_type = status.table_type
        row_id = status.row_id

        # Fetch the corresponding record
        record = mem_db.records[table_type].get(row_id)
        if record:
            record.status_id = status.id

    # Rebuild meta_group structure for services.
    for table_type in group_maps:
        for group_id in group_maps[table_type]:
            group = group_maps[table_type][group_id]
            status_id = group[0].status_id
            status = mem_db.statuses[status_id].status
            mem_db.add_work(group[0].af, table_type, group, group_id, STATUS_INIT)
