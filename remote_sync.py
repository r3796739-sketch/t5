import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect('76.13.245.213', username='root', password='Apjabdulkapam@123')
    sftp = ssh.open_sftp()
    
    # Download nginx config
    sftp.get('/etc/nginx/sites-available/yoppychat', 'c:/Users/Nikhil/Desktop/t5-whatsapp-update/yoppychat_nginx.conf')
    sftp.close()
    ssh.close()
    print("SUCCESS: Downloaded Nginx config.")
except Exception as e:
    print(f"ERROR: {e}")
