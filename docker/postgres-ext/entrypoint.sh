#!/usr/bin/env bash

set -o errexit
set -o nounset

# ANSI color codes
GREEN='\033[0;32m'
NC='\033[0m'

INIT_DIR="/docker-entrypoint-initdb.d"

DB_USER_1="${DB_USER_1:-postgres}"
DB_PASS_1="${DB_PASS_1:-postgres}"
DB_NAME_1="${DB_NAME_1:-postgres}"

DB_USER_2="${DB_USER_2:-}"
DB_PASS_2="${DB_PASS_2:-}"
DB_NAME_2="${DB_NAME_2:-}"

DB_USER_3="${DB_USER_3:-}"
DB_PASS_3="${DB_PASS_3:-}"
DB_NAME_3="${DB_NAME_3:-}"

# ---------------------------------------------------------------------------
# Generate idempotent init SQL from the DB_USER_N / DB_PASS_N / DB_NAME_N env
# groups. The official postgres entrypoint executes every *.sql in
# /docker-entrypoint-initdb.d on first init (after POSTGRES_USER / POSTGRES_DB
# already exist), so the WHERE NOT EXISTS guards make reruns safe.
#
# 01-create-roles.sql     → CREATE ROLE ... LOGIN PASSWORD (only when DB_USER_N set)
# 02-create-databases.sql → CREATE DATABASE ... OWNER      (only when DB_NAME_N set)
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
      local sql_pass
      sql_pass="'${_pass//\'/\'\'}'"
      local sql_stmt
      sql_stmt="CREATE ROLE \"${_user}\" LOGIN PASSWORD ${sql_pass}"
      local escaped_stmt
      escaped_stmt="${sql_stmt//\'/\'\'}"
      roles_sql+="SELECT '${escaped_stmt}' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${_user}')\gexec"$'\n'
      wrote=true
      wrote=true
    fi

    if [ -n "${_name}" ]; then
      if [ -n "${_user}" ]; then
        dbs_sql+="SELECT 'CREATE DATABASE \"${_name}\" OWNER \"${_user}\"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${_name}')\gexec"$'\n'
      else
        dbs_sql+="SELECT 'CREATE DATABASE \"${_name}\"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${_name}')\gexec"$'\n'
      fi
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

# Delegate to the official postgres entrypoint — it runs our generated SQL
# (plus baked-in 00-extensions.sql) and manages the server lifecycle.
exec /usr/local/bin/docker-entrypoint.sh "$@"
