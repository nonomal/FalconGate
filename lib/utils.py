import socket
import base64
import sqlite3 as lite
import subprocess
import threading
import time
import re
from struct import unpack
from socket import AF_INET, inet_pton
import pickle
from lib.logger import *
import os
import sys
import gc
import json
import fileinput
from lib.settings import homenet, lock


class CleanOldHomenetObjects(threading.Thread):
    def __init__(self, threadID):
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.ctime = int(time.time())
        self.bro_file_path = '/opt/zeek/logs/current/extract_files/'

    def run(self):

        while 1:
            self.ctime = int(time.time())
            try:
                self.clean_old_host_objects()
            except Exception as e:
                log.debug('FG-WARN: ' + str(e.__doc__) + " - " + str(e))
            time.sleep(600)

    def clean_old_host_objects(self):

        with lock:
            for k in homenet.hosts.keys():
                # Cleaning old DNS entries
                for l in homenet.hosts[k].dns.keys():
                    if (self.ctime - homenet.hosts[k].dns[l].ts) > 86400:
                        del homenet.hosts[k].dns[l]

                # Cleaning old connections
                for l in homenet.hosts[k].conns.keys():
                    if (self.ctime - homenet.hosts[k].conns[l].lseen) > 3600:
                        del homenet.hosts[k].conns[l]

                # Cleaning old files
                for l in homenet.hosts[k].files.keys():
                    if (self.ctime - homenet.hosts[k].files[l].ts) > 604800:
                        del homenet.hosts[k].files[l]

                # Cleaning Bro carved files older than 10 minutes
                now = time.time()
        try:
            for f in os.listdir(self.bro_file_path):
                target = os.path.join(self.bro_file_path, f)
                if os.stat(target).st_mtime < now - 600:
                    os.remove(target)
        except Exception:
            pass


def domain_resolver(domain):
    record = socket.gethostbyname_ex(domain)
    return record[2]


def get_sld(query):
    fields = query.split(".")
    if len(fields) == 2:
        return query
    elif len(fields) > 2:
        return fields[-2] + "." + fields[-1]
    else:
        return None


def get_tld(query):
    fields = query.split(".")
    if len(fields) >= 2:
        return fields[-1]
    else:
        return None


def encode_base64(s):
    return base64.b64encode(s)


def decode_base64(s):
    return base64.b64decode(s)


def get_vendor(mac):
        con = lite.connect('db/vendors.sqlite')
        with con:
            cur = con.cursor()
            tmac = mac.replace(':', '').upper()

            cur.execute("SELECT vendor from vendors where mac_id=?", (tmac[0:6],))
            row = cur.fetchone()
            if row:
                return row[0]
            else:
                pass
        con.close()


def create_alert_db():
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        try:
            cur.execute("create table alerts(id int, type text, fseen int, lseen int, lrep int, "
                        "nrep int, threat text, sip text, ind text, handled int, desc text, ref text, primary key "
                        "(sip, ind))")
            cur.execute("create index id_idx on alerts (id)")
            cur.execute("create index type_idx on alerts (type)")
            cur.execute("create index nrep_idx on alerts (nrep)")
            cur.execute("create index threat_idx on alerts (threat)")
            cur.execute("create index sip_idx on alerts (sip)")
            cur.execute("create index handled_idx on alerts (handled)")
        except lite.OperationalError as e:
            pass


def add_alert_to_db(alert):
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        cur.execute('select * from alerts')
        res = cur.fetchone()
        if not res:
            alert[0] = 1
            lid = 1
        else:
            cur.execute('select MAX(id) from alerts')
            res = cur.fetchone()
            lid = res[0] + 1
            alert[0] = lid

        try:
            cur.execute("insert into alerts values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", alert)
        except lite.IntegrityError:
            ctime = int(time.time())
            cur.execute("update alerts set lseen = ? where sip = ? and ind = ?", (ctime, alert[7], alert[8]))
    con.commit()
    con.close()
    return lid


def get_not_reported_alerts():
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        try:
            cur.execute("select * from alerts where nrep = 0 order by fseen asc")
            res = cur.fetchall()
        except lite.OperationalError:
            res = None
    con.close()
    return res


def get_latest_alerts(nalerts):
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        try:
            cur.execute("select * from alerts order by id desc limit ?", (nalerts,))
            res = cur.fetchall()
        except lite.OperationalError:
            res = None
    con.close()
    return res


def get_alerts_within_time(tframe, handled):
    con = lite.connect('logs/alerts.sqlite')
    ctime = int(time.time())
    ttime = ctime - tframe
    with con:
        cur = con.cursor()
        try:
            if handled == "all":
                cur.execute("select * from alerts where fseen > ? order by fseen desc", (str(ttime),))
            else:
                cur.execute("select * from alerts where fseen > ? and handled = ? order by fseen desc", (str(ttime), handled))
            res = cur.fetchall()
        except lite.OperationalError:
            res = ['none']
    con.close()
    return json.dumps(res)


def update_alert_nrep(alert_id, nrep):
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        cur.execute("update alerts set nrep = ? where id = ?", (nrep, alert_id))
    con.commit()
    con.close()


def validate_ip(ip):
    aa = re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip)
    if aa:
        return True
    else:
        return False


def validate_domain(domain):
    aa = re.match(r"^([a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}$", domain)
    if aa:
        return True
    else:
        return False


def validate_base64(target_str):
    aa = re.match(r"^(?:[A-Za-z0-9+\/\/n]{4})*(?:[A-Za-z0-9+\/]{2}==|[A-Za-z0-9+\/]{3}=|[A-Za-z0-9+\/]{4})$", target_str)
    if aa:
        return True
    else:
        return False


def lookup(ip):
    f = unpack('!I', inet_pton(AF_INET, ip))[0]
    private = (
        [2130706432, 4278190080], # 127.0.0.0,   255.0.0.0   http://tools.ietf.org/html/rfc3330
        [3232235520, 4294901760], # 192.168.0.0, 255.255.0.0 http://tools.ietf.org/html/rfc1918
        [2886729728, 4293918720], # 172.16.0.0,  255.240.0.0 http://tools.ietf.org/html/rfc1918
        [167772160,  4278190080], # 10.0.0.0,    255.0.0.0   http://tools.ietf.org/html/rfc1918
    )
    for net in private:
        if (f & net[1]) == net[0]:
            return True
    return False


def flush_ipset_list(list_name):
    p = subprocess.Popen(["ipset", "flush", list_name], stdout=subprocess.PIPE)
    output, err = p.communicate()


def restore_ipset_blacklist(fpath):
    cmd = "ipset restore < " + fpath
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()


def add_ip_ipset_blacklist(ip, listname):
    cmd = "ipset add {} {}".format(listname, ip)
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()


def del_ip_ipset_blacklist(ip, listname):
    cmd = "ipset del {} {}".format(listname, ip)
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()


def list_ipset_blacklist(listname):
    cmd = "ipset list {}".format(listname)
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()
    lines = output.split('\n')
    return lines


def restart_dnsmasq():
    cmd = "service dnsmasq restart"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()


def reboot_appliance():
    cmd = "reboot"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, err = p.communicate()


def reset_appliance():
    os.system("rm -f logs/alerts.sqlite")
    os.system("cp install/Raspbian/templates/user_config.ini.tpl html/user_config.ini")
    os.system("cp install/Raspbian/templates/pwd.db.tpl html/pwd.db")
    os.system("chmod www-data: html/user_config.ini")
    os.system("chmod www-data: html/pwd.db")
    restart_falcongate_service()


def restart_falcongate_service():
    sys.stdout.flush()
    time.sleep(1)
    gc.collect()
    os.system("service falcongate restart")


def save_pkl_object(obj, filename):
    with open(filename, 'wb') as output:
        pickle.dump(obj, output, pickle.HIGHEST_PROTOCOL)
    return True


def load_pkl_object(filename):
    obj = pickle.load(open(filename, "rb"))
    return obj


def get_syslogs(log_count):
    logs = []
    logs_to_return = []

    with open("/var/log/syslog") as f:
        for oline in f:
            logs.append(oline)

        count = 0
        logs_len = len(logs)
        for line in logs[:-logs_len:-1]:
            if count <= log_count:
                if "FG-" in line:
                    logs_to_return.append(line)
                    count += 1
                else:
                    pass
            else:
                break
    return json.dumps(logs_to_return)


def reconfigure_network(old_gw, new_gw):
    target_files = ['/etc/network/interfaces', '/etc/dnsmasq.conf', '/etc/nginx/sites-available/default']
    octects = str(old_gw).split(".")
    t_old_gw = '.'.join(octects[0:3])
    octects = str(new_gw).split(".")
    t_new_gw = '.'.join(octects[0:3])
    for f in target_files:
        for line in fileinput.input(f, inplace=1):
            line = re.sub(old_gw, new_gw, line.rstrip())
            line = re.sub(t_old_gw, t_new_gw, line.rstrip())


def update_alert_handled(alert_id, handled):
    con = lite.connect('logs/alerts.sqlite')
    with con:
        cur = con.cursor()
        cur.execute("update alerts set handled = ? where id = ?", (handled, alert_id))
    con.commit()
    con.close()


def get_active_devices():
    devices = []
    try:
        for k in homenet.hosts.keys():
            device = {'mac': str(homenet.hosts[k].mac), 'ip': str(homenet.hosts[k].ip), 'vendor': str(homenet.hosts[k].vendor),
                      'tcp_ports': homenet.hosts[k].tcp_ports, 'udp_ports': homenet.hosts[k].udp_ports,
                      'hostname': str(homenet.hosts[k].hostname)}
            devices.append(device)
    except Exception:
        pass

    return json.dumps(devices)


def get_network_config():
    netconfig = []
    try:
        netconfig = {'interface': str(homenet.interface), 'ip': str(homenet.ip), 'gateway': str(homenet.gateway),
                     'netmask': str(homenet.netmask), 'mac': str(homenet.mac)}
    except Exception:
        pass

    return json.dumps(netconfig)


def ping_host(ip):
    response = os.system("ping -c 1 -w2 " + ip + " > /dev/null 2>&1")

    if response == 0:
        return True
    else:
        return False


def is_file_executable(file):
    output = subprocess.check_output(['file', file])
    if ("Installer" in output) or ("executable" in output):
        return True
    else:
        return False
