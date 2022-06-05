create user 'replicator'@'%' identified with mysql_native_password by 'replicator';
grant replication slave on *.* to 'replicator'@'%';
flush privileges;
