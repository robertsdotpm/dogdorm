BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "settings" (
	"key"	TEXT,
	"value"	INTEGER,
	PRIMARY KEY("key")
);
CREATE TABLE IF NOT EXISTS "status" (
	"id"	INTEGER,
	"table_type"	INTEGER CHECK("table_type" BETWEEN 14 AND 16),
	"row_id"	INTEGER,
	"status"	INTEGER NOT NULL DEFAULT 0 CHECK("status" BETWEEN 9 AND 13),
	"last_status"	INTEGER NOT NULL CHECK("last_status" >= 1735689600 AND "last_status" <= 32503680000),
	"test_no"	INTEGER NOT NULL DEFAULT 0,
	"failed_tests"	INTEGER NOT NULL DEFAULT 0 CHECK("failed_tests" <= "test_no"),
	"last_success"	INTEGER NOT NULL DEFAULT 0 CHECK("last_success" = 0 OR ("last_success" >= 1735689600 AND "last_success" <= 32503680000)),
	"uptime"	INTEGER NOT NULL DEFAULT 0,
	"max_uptime"	INTEGER NOT NULL DEFAULT 0,
	"last_uptime"	INTEGER NOT NULL DEFAULT 0 CHECK("last_uptime" = 0 OR ("last_uptime" >= 1735689600 AND "last_uptime" <= 32503680000)),
	PRIMARY KEY("id")
);
CREATE TABLE IF NOT EXISTS "aliases" (
	"id"	INTEGER,
	"fqn"	TEXT NOT NULL,
	"af"	INTEGER NOT NULL CHECK("af" IN (2, 10)),
	"ip"	TEXT DEFAULT NULL,
	"group_id"	INTEGER,
	"table_type"	INTEGER DEFAULT 15,
	PRIMARY KEY("id"),
	UNIQUE("fqn","af")
);
CREATE TABLE IF NOT EXISTS "services" (
	"id"	INTEGER,
	"type"	INTEGER NOT NULL CHECK("type" BETWEEN 3 AND 8),
	"af"	INTEGER NOT NULL CHECK("af" IN (2, 10)),
	"proto"	INTEGER NOT NULL CHECK("proto" IN (1, 2)),
	"ip"	TEXT NOT NULL,
	"port"	INTEGER NOT NULL CHECK("port" BETWEEN 1 AND 65535),
	"group_id"	INTEGER NOT NULL,
	"user"	TEXT DEFAULT NULL,
	"password"	TEXT DEFAULT NULL,
	"alias_id"	INTEGER DEFAULT NULL,
	"table_type"	INTEGER DEFAULT 14,
	UNIQUE("type","af","proto","ip","port"),
	PRIMARY KEY("id"),
	FOREIGN KEY("alias_id") REFERENCES "aliases"("id")
);
CREATE TABLE IF NOT EXISTS "imports" (
	"id"	INTEGER,
	"type"	INTEGER NOT NULL CHECK("type" BETWEEN 3 AND 8),
	"af"	INTEGER NOT NULL CHECK("af" IN (2, 10)),
	"ip"	TEXT,
	"port"	INTEGER NOT NULL CHECK("port" BETWEEN 1 AND 65535),
	"user"	TEXT DEFAULT NULL,
	"password"	TEXT DEFAULT NULL,
	"alias_id"	INTEGER DEFAULT NULL,
	"group_id"	INTEGER,
	"table_type"	INTEGER DEFAULT 16,
	"proto"	INTEGER DEFAULT NULL,
	""	INTEGER,
	PRIMARY KEY("id"),
	FOREIGN KEY("alias_id") REFERENCES "aliases"("id"),
	UNIQUE("type","af","ip","port")
);
COMMIT;
