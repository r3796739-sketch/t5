import paramiko
import time
import argparse

def setup_server(host, username, password, domain, worker_count=8):
    print(f"--- Connecting to New VPS: {host} ---")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(host, username=username, password=password)
        print("✅ Connected successfully!\n")
        
        # 1. Update and install dependencies
        print("1. Installing system dependencies (this will take a minute)...")
        commands = [
            "apt-get update -y",
            "apt-get install -y python3-venv python3-pip nginx supervisor git redis-server certbot python3-certbot-nginx",
            "systemctl enable redis-server",
            "systemctl start redis-server",
            "mkdir -p /root/t5",
            "mkdir -p /var/www/yoppychat_static"
        ]
        for cmd in commands:
            stdin, stdout, stderr = client.exec_command(cmd)
            stdout.channel.recv_exit_status() # wait for it to finish
            
        print("✅ System dependencies installed.\n")

        # 2. Setup Gunicorn Configuration
        print("2. Configuring Gunicorn & CPU limits...")
        gunicorn_conf = f"""import multiprocessing
import os

bind = "unix:/root/t5/gunicorn.sock"
workers = {worker_count}
worker_class = "sync"
timeout = 180
keepalive = 5

accesslog = "-"
errorlog  = "-"
loglevel  = "info"
proc_name = "yoppychat"
"""
        
        run_file(client, '/root/t5/gunicorn.conf.py', gunicorn_conf)

        # 3. Setup Supervisor (Background processes)
        print("3. Configuring Supervisor for Gunicorn and Huey...")
        super_gunicorn = """[program:gunicorn]
command=/root/t5/venv/bin/gunicorn --config /root/t5/gunicorn.conf.py app:app
directory=/root/t5
user=root
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/gunicorn.err.log
stdout_logfile=/var/log/gunicorn.out.log
"""
        run_file(client, '/etc/supervisor/conf.d/gunicorn.conf', super_gunicorn)

        super_huey = """[program:huey]
command=/root/t5/venv/bin/python run_worker.py
directory=/root/t5
user=root
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/huey.err.log
stdout_logfile=/var/log/huey.out.log
"""
        run_file(client, '/etc/supervisor/conf.d/huey.conf', super_huey)

        # 4. Setup Nginx (Web Server routing)
        print("4. Configuring Nginx routing...")
        nginx_conf = f"""server {{
    listen 80;
    server_name {domain};

    # Serve static frontend files automatically
    location /static/ {{
        alias /var/www/yoppychat_static/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }}

    location / {{
        proxy_pass http://unix:/root/t5/gunicorn.sock;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Increase timeouts for slow AI requests
        proxy_read_timeout 180s;
        proxy_connect_timeout 180s;
        proxy_send_timeout 180s;
    }}
}}
"""
        run_file(client, f'/etc/nginx/sites-available/{domain}', nginx_conf)
        
        # Link nginx and restart
        client.exec_command(f"ln -s /etc/nginx/sites-available/{domain} /etc/nginx/sites-enabled/")
        client.exec_command("rm /etc/nginx/sites-enabled/default") # remove default page
        client.exec_command("systemctl restart nginx")
        
        # Reload supervisor
        client.exec_command("supervisorctl reread && supervisorctl update")

        print("✅ Server architecture completely configured!\n")
        print("----------------------------------------------------------------")
        print("FINAL STEPS:")
        print("1. Run your 'upload_to_server.py' to push all your files to /root/t5")
        print("2. SSH in and run: cd /root/t5 && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt")
        print("3. Run: supervisorctl restart all")
        print(f"4. Map your domain DNS to {host} and run: certbot --nginx -d {domain}")
        print("----------------------------------------------------------------")
        
    except Exception as e:
        print(f"❌ Error connecting or setting up server: {e}")
    finally:
        client.close()

def run_file(client, path, content):
    """Helper to write string content to remote file"""
    with client.open_sftp() as sftp:
        with sftp.file(path, 'w') as f:
            f.write(content)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-Setup an empty Ubuntu VPS for YoppyChat")
    parser.add_argument('--ip', required=True, help="IP address of the new server")
    parser.add_argument('--password', required=True, help="Root SSH password")
    parser.add_argument('--domain', required=True, help="Your domain name (e.g. app.yoppychat.com)")
    parser.add_argument('--workers', type=int, default=8, help="Number of concurrent workers (8 for 4GB RAM, 16 for 8GB RAM)")
    
    args = parser.parse_args()
    setup_server(args.ip, 'root', args.password, args.domain, args.workers)
