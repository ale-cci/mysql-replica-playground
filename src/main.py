import os
import time
import docker

def get_volume_name(ident):
    return f'data-replica-{ident}'

def get_mysql_master_container(cli):
    cs = cli.containers.list(filters={
        'name': 'master',
    })
    if len(cs) > 1:
        raise RuntimeError('Multiple containers with name "master" found')
    master_container = cs[0]

    if master_container.status != 'running':
        raise RuntimeError('MySQL master is not "running"')

    return master_container


def start_replica(cli, ident, prune_data=False):
    if prune_data:
        ensure_empty_volume(cli, get_volume_name(ident))

    replica = start_mysql(cli, ident)
    # time.sleep(10)

    RETRIES = 3
    for i in range(RETRIES):
        time.sleep(10)
        ec = replica.exec_run('''mysql -A -e "
    CHANGE MASTER TO MASTER_HOST='master', MASTER_USER='replicator', MASTER_PASSWORD='replicator';
    RESET REPLICA;
    START REPLICA;
    "
    ''')
        if ec.exit_code == 0:
            print(ec.output.decode('utf-8'))
            break
    else:
        print(ec.output.decode('utf-8'))
        print(replica.logs())
        replica.stop()
        replica.wait()
        raise RuntimeError('Unable to initialize mysql replica')
    return replica


def start_mysql(cli, ident, readonly=True):
    replica_data = get_volume_name(ident)

    master_net = get_mysql_master_container(cli)
    nets = master_net.attrs['NetworkSettings']['Networks']
    if len(nets) > 1:
        raise RuntimeError('TODO: master container is in multiple networks')

    readonly_flag = '--read-only' if readonly else ''

    net = next(iter(nets.values()))
    net_id = net['NetworkID']

    docker_data_dir = os.path.abspath('../docker-data')
    return cli.containers.run(
        name=f'replica-{ident}',
        image='mysql:8',
        detach=True,
        remove=True,
        auto_remove=True,
        entrypoint=f'/slave-entrypoint.sh --server-id={ident} {readonly_flag}',
        volumes=[
            f'{replica_data}:/var/lib/mysql',
            f'{docker_data_dir}/init-slave.sql:/docker-entrypoint-initdb.d/init.sql',
            f'{docker_data_dir}/entrypoint-slave.sh:/slave-entrypoint.sh',
        ],
        network=net_id,
    )

def copy_data(cli, from_container, to_container):

    out = cli.containers.run(
        name='copy-data',
        image='busybox:latest',
        remove=True,
        command='sh -c "cd /src; tar cf - . | (cd /dest; tar xvf -)"',
        volumes=[
            f'{from_container}:/src/',
            f'{to_container}:/dest/',
        ]
    )


def ensure_empty_volume(cli, volume_name):
    try:
        vol = cli.volumes.get(volume_name)
        vol.remove()
    except docker.errors.NotFound:
        pass
    return cli.volumes.create(volume_name)


def align(cli, replica_ident, ident):
    if replica_ident == ident:
        raise RuntimeError('Async replica and destination must differ')
    replica = cli.containers.get(f'replica-{replica_ident}')
    replica.exec_run('mysql -A < "STOP REPLICA"')

    dest_volume = get_volume_name(ident)
    replica_volume = get_volume_name(replica_ident)

    try:
        dest = cli.containers.get(f'replica-{ident}')
        dest.stop()
        dest.wait()
    except docker.errors.NotFound:
        pass

    ensure_empty_volume(cli, dest_volume)
    copy_data(cli, replica_volume, dest_volume)

    replica.exec_run('mysql -A < "START REPLICA"')

    container = start_mysql(cli, ident, readonly=False)


def start_nginx(cli):
    nginx_conf = os.path.abspath('./nginx.conf')
    return cli.containers.run(
        image='nginx:latest',
        name='mysql-staging',
        ports={
            '3306/tcp': 3307,
        },
        volumes=[
            f'{nginx_conf}:/etc/nginx/nginx.conf',
        ],
        network='mysql-cluster_default',
        detach=True,
        remove=True,
        auto_remove=True,
    )

def update_nginx_config(staging_name):
    with open('./nginx.conf', 'w') as fd:
        fd.write('''
        stream {
            server {
                listen 3306;
                proxy_pass %s:3306;
            }
        }
        events {}
        ''' % staging_name)


def ensure_stopped(cli, *names):
    for name in names:
        try:
            # Stop previous container
            c = cli.containers.get(name)
            c.stop()
            c.wait()
        except docker.errors.NotFound:
            pass

def main(cli):
    master = get_mysql_master_container(cli)

    ensure_stopped(cli, 'mysql-staging', 'replica-2', 'replica-3', 'replica-4')
    # primary replica
    replica_ident = 2
    replica = start_replica(cli, ident=2, prune_data=True)


    # Only one staging replica must be active and allow rw connections on it,
    # The other is shut down
    staging_replica_id = [3, 4]

    active_replica = 0

    ensure_empty_volume(cli, get_volume_name(staging_replica_id[active_replica]))
    start_mysql(cli, staging_replica_id[active_replica], readonly=False)

    update_nginx_config(f'replica-{staging_replica_id[active_replica]}')
    nginx = start_nginx(cli)

    try:
        while True:
            print(f'Current master: {staging_replica_id[active_replica]}')

            active_replica = (active_replica + 1) % len(staging_replica_id)
            new_ident = staging_replica_id[active_replica]

            new_container_name = f'replica-{new_ident}'

            ensure_stopped(cli, new_container_name)

            t = input('Updated. Press a key to continue')
            align(cli, replica_ident, new_ident)

            update_nginx_config(new_container_name)
            nginx.exec_run('nginx -s reload')

    except KeyboardInterrupt:
        print('Stopping containers...')
        replica.stop()
        nginx.stop()

if __name__ == '__main__':
    cli = docker.from_env()
    main(cli)

