#!/usr/bin/env bash

set -o errexit
set -o nounset

# ANSI color codes
GREEN='\033[0;32m'
NC='\033[0m'

# Overridable so the generator can be exercised without touching the real
# init directory (the official entrypoint only reads the default path).
INIT_DIR="${INIT_DIR:-/docker-entrypoint-initdb.d}"

DB_USER_1="${DB_USER_1:-}"
DB_PASS_1="${DB_PASS_1:-}"
DB_NAME_1="${DB_NAME_1:-}"

DB_USER_2="${DB_USER_2:-}"
DB_PASS_2="${DB_PASS_2:-}"
DB_NAME_2="${DB_NAME_2:-}"

DB_USER_3="${DB_USER_3:-}"
DB_PASS_3="${DB_PASS_3:-}"
DB_NAME_3="${DB_NAME_3:-}"

# psql reads these with ``\getenv``, i.e. from the process environment — so the
# effective values must be exported before the official entrypoint runs psql.
# Without this the ``\getenv`` lines fail silently when a variable is unset and
# the literal ``:'user_3'`` reaches the server ("syntax error at or near \":\"").
export DB_USER_1 DB_PASS_1 DB_NAME_1 \
       DB_USER_2 DB_PASS_2 DB_NAME_2 \
       DB_USER_3 DB_PASS_3 DB_NAME_3

# ---------------------------------------------------------------------------
# Generate idempotent init SQL from the DB_USER_N / DB_PASS_N / DB_NAME_N env
# groups. The official postgres entrypoint executes every *.sql in
# /docker-entrypoint-initdb.d on first init (after POSTGRES_USER / POSTGRES_DB
# already exist), so the WHERE NOT EXISTS guards make reruns safe.
#
# 01-create-roles.sql     → CREATE ROLE ... LOGIN PASSWORD (only when DB_USER_N set)
# 02-create-databases.sql → CREATE DATABASE ... OWNER      (only when DB_NAME_N set)
#
# The values never enter the SQL text.  psql reads each one from the
# environment with ``\getenv`` and the statement is built server-side with
# ``format(%I, ...)`` / ``format(%L, ...)`` — PostgreSQL's own identifier and
# literal quoters — then executed with ``\gexec``.  Hand-quoting them in bash
# was wrong twice over: ``CREATE ROLE "name"`` never doubled an embedded
# double quote, and ``rolname = 'name'`` never doubled an embedded single
# quote.  Here neither character needs any bash-level escaping.
# ---------------------------------------------------------------------------

generate_init_sql() {
  local i
  local roles_sql=""
  local dbs_sql=""
  local wrote=false

  for i in 1 2 3; do
    local -n _user="DB_USER_${i}"
    local -n _pass="DB_PASS_${i}"
    local -n _name="DB_NAME_${i}"

    if [ -n "${_user}" ]; then
      roles_sql+="\\getenv user_${i} DB_USER_${i}"$'\n'
      roles_sql+="\\getenv pass_${i} DB_PASS_${i}"$'\n'
      roles_sql+="SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user_${i}', :'pass_${i}')"$'\n'
      roles_sql+="WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'user_${i}')"$'\n'
      roles_sql+="\\gexec"$'\n'
      wrote=true
    fi

    if [ -n "${_name}" ]; then
      dbs_sql+="\\getenv db_${i} DB_NAME_${i}"$'\n'
      if [ -n "${_user}" ]; then
        dbs_sql+="\\getenv user_${i} DB_USER_${i}"$'\n'
        dbs_sql+="SELECT format('CREATE DATABASE %I OWNER %I', :'db_${i}', :'user_${i}')"$'\n'
      else
        dbs_sql+="SELECT format('CREATE DATABASE %I', :'db_${i}')"$'\n'
      fi
      dbs_sql+="WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_${i}')"$'\n'
      dbs_sql+="\\gexec"$'\n'
      wrote=true
    fi
  done

  if [ "${wrote}" = true ]; then
    printf '%s' "${roles_sql}" > "${INIT_DIR}/01-create-roles.sql"
    printf '%s' "${dbs_sql}" > "${INIT_DIR}/02-create-databases.sql"
    echo -e "${GREEN}Generated init SQL for configured roles/databases${NC}"
  else
    echo -e "${GREEN}No DB_USER_*/DB_NAME_* env groups set — skipping init SQL generation${NC}"
  fi
}

generate_init_sql

# The socket directory is a bind mount, so the image's postgres-owned
# /var/run/postgresql is replaced by the host directory's ownership. Docker
# creates a missing host path as root:root, and /var/run is tmpfs on the host
# (recreated after every reboot), so re-own the directory here — this script
# runs as root — or the postmaster cannot create .s.PGSQL.5432.
mkdir -p /var/run/postgresql
chown postgres:postgres /var/run/postgresql
chmod 3777 /var/run/postgresql

# Delegate to the official postgres entrypoint — it runs our generated SQL
# (plus baked-in 00-extensions.sql) and manages the server lifecycle.
exec /usr/local/bin/docker-entrypoint.sh "$@"
