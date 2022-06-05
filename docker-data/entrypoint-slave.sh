#!/usr/bin/env bash
source /entrypoint.sh

setup_env() {
    declare -g DATADIR SOCKET
    DATADIR="$(mysql_get_config 'datadir' "$@")"
    SOCKET="$(mysql_get_config 'socket' "$@")"

    declare -g DATABASE_ALREADY_EXISTS
	if [ -d "$DATADIR/mysql" ]; then
		DATABASE_ALREADY_EXISTS='true'
	fi
}

_main() {
    echo "$@"
    if [ "${1:0:1}" = '' ]; then
        set -- mysqld "$@"
    fi
    if [ "${1:0:1}" = '-' ]; then
        set -- mysqld "$@"
    fi

    if [ "$1" = 'mysqld' ] && ! _mysql_want_help "$@"; then
        setup_env "$@"
        docker_create_db_directories

        if [ "$(id -u)" = "0" ]; then
            mysql_note "Switching to dedicated user 'mysql'"
            exec gosu mysql "$BASH_SOURCE" "$@"
        fi

        if [ -z "$DATABASE_ALREADY_EXISTS" ]; then
            docker_init_database_dir "$@"

            mysql_note "Starting temporary server"
            docker_temp_server_start "$@"
            mysql_note "Temporary server started."

            docker_process_init_files /docker-entrypoint-initdb.d/*

            mysql_note "Stopping temporary server"
            docker_temp_server_stop
            mysql_note "Temporary server stopped"
        fi
    fi
    mysql_note "MySQL init process done. Ready for start up."
    exec "$@"
}

_main "$@"
