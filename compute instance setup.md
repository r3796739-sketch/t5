YoppyChat: Production Deployment Guide for Google CloudThis document provides a comprehensive guide to deploying the YoppyChat web application on a fresh Google Cloud Compute Engine instance running Debian 12.Deployment Stack:Cloud Provider: Google Cloud Platform (Compute Engine)Operating System: Debian 12 (Bookworm)Web Server: NginxApplication Server: Gunicorn (with gevent workers)Process Manager: SupervisorTask Queue: Huey with RedisDatabase & Auth: Supabase (External)Part 1: Initial Server Setup1. Create a Google Cloud VM InstanceNavigate to the Google Cloud Console and go to Compute Engine > VM instances.Click Create Instance.Name: Choose a name (e.g., yoppychat-prod-server).Region & Zone: Select a region that is geographically close to your Supabase database instance to minimize latency.Machine Configuration:Series: E2Machine Type: e2-standard-2 (2 vCPUs, 8 GB memory) is recommended for production.Boot Disk:Click Change.Operating System: DebianVersion: Debian GNU/Linux 12 (bookworm)Click Select.Firewall:Check Allow HTTP traffic.Check Allow HTTPS traffic.Click Create.2. Connect via SSHOnce the instance is created, click the SSH button to open a terminal connection in your browser. All subsequent commands will be run in this terminal.3. Create a Non-Root UserFor security, we will not run the application as the root user.# Replace 'deploy_user' with a username of your choice
sudo adduser deploy_user

# Grant the new user sudo (administrator) privileges
sudo usermod -aG sudo deploy_user

# Switch to the new user's session
su - deploy_user
Part 2: Install System Dependencies1. Update System Packagessudo apt update && sudo apt upgrade -y
2. Install Nginx, Python, Redis, and Gitsudo apt install -y nginx python3-venv python3-pip redis-server git
3. Install Supervisorsudo apt install -y supervisor
Part 3: Application Setup1. Clone Your Project RepositoryClone your application code from your Git repository.# Replace with your repository URL
git clone [https://github.com/your-username/yoppychat.git](https://github.com/your-username/yoppychat.git) ~/yoppychat
cd ~/yoppychat
2. Create Python Virtual Environment# Create the virtual environment inside your project folder
python3 -m venv venv

# Activate the environment
source venv/bin/activate
3. Install Python DependenciesInstall all required libraries from your requirements.txt file, including gevent for asynchronous workers.pip install -r requirements.txt
pip install gevent
4. Configure Environment VariablesCreate a .env file in your project root and populate it with your secret keys and configuration.# Create and open the file for editing
nano .env
Paste the contents of your local .env file here, ensuring all API keys (Supabase, Google, Groq, Razorpay, etc.) are correct for your production environment.Part 4: Database Setup (Supabase)Your database is hosted on Supabase, so no local installation is needed. However, you must run your setup script to create the tables and functions.Log in to your Supabase project dashboard.Go to the SQL Editor.Click New query.Copy the entire content of your database_setup.sql file and paste it into the editor.Click Run. This will prepare your database schema.Part 5: Process Management with SupervisorWe will use Supervisor to manage our Gunicorn, Huey, and Discord services, ensuring they run continuously and restart automatically if they crash.1. Gunicorn ConfigurationCreate a Supervisor config file for Gunicorn.sudo nano /etc/supervisor/conf.d/gunicorn.conf
Paste the following configuration. This is optimized for your e2-standard-2 instance.[program:gunicorn]
command=/home/deploy_user/yoppychat/venv/bin/gunicorn -k gevent --workers 3 --bind unix:/home/deploy_user/yoppychat/gunicorn.sock app:app
directory=/home/deploy_user/yoppychat
user=deploy_user
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/gunicorn.err.log
stdout_logfile=/var/log/gunicorn.out.log
2. Huey Worker ConfigurationCreate a Supervisor config file for your Huey background tasks.sudo nano /etc/supervisor/conf.d/huey.conf
Paste the following configuration. We are setting workers to 2 to balance the load on your 2-CPU server.[program:huey]
command=/home/deploy_user/yoppychat/venv/bin/python run_worker.py
directory=/home/deploy_user/yoppychat
user=deploy_user
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/huey.err.log
stdout_logfile=/var/log/huey.out.log
Note: Ensure your run_worker.py is configured with --workers 2.3. Discord Service ConfigurationCreate a Supervisor config file for your Discord bot service.sudo nano /etc/supervisor/conf.d/discord.conf
Paste the following configuration.[program:discord]
command=/home/deploy_user/yoppychat/venv/bin/python discord_service.py
directory=/home/deploy_user/yoppychat
user=deploy_user
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/discord.err.log
stdout_logfile=/var/log/discord.out.log
4. Load and Start ServicesTell Supervisor to read the new configuration files and start the services.sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start gunicorn huey discord
Part 6: Web Server with Nginx1. Create Nginx Server BlockCreate a configuration file for your site. Replace your_domain with your actual domain name.sudo nano /etc/nginx/sites-available/your_domain
Paste the following production-ready configuration. It is configured to serve static files directly, handle streaming correctly, and pass all other requests to Gunicorn via the Unix socket.server {
    listen 80;
    server_name your_domain www.your_domain;

    # Serve static files directly
    location /static/ {
        alias /home/deploy_user/yoppychat/static/;
        expires 1d;
        access_log off;
    }

    # Handle real-time streaming
    location /stream_answer {
        proxy_pass http://unix:/home/deploy_user/yoppychat/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
    }

    # Handle all other requests
    location / {
        proxy_pass http://unix:/home/deploy_user/yoppychat/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
2. Enable the SiteCreate a symbolic link to enable the new site configuration.sudo ln -s /etc/nginx/sites-available/your_domain /etc/nginx/sites-enabled
3. Fix Socket PermissionsGrant Nginx's user (www-data) permission to access the Gunicorn socket in your home directory.# Add the www-data user to your user group
sudo usermod -a -G deploy_user www-data

# Give your group execute permissions on your home directory
chmod 710 /home/deploy_user
4. Restart NginxTest the configuration and restart the Nginx service.sudo nginx -t
sudo systemctl restart nginx
At this point, your site should be accessible via HTTP at http://your_domain.Part 7: Secure Your Site with HTTPSWe will use Certbot with Let's Encrypt to get a free SSL certificate.Install Certbot:sudo apt install -y certbot python3-certbot-nginx
Run Certbot: It will automatically detect your domain from the Nginx config, obtain a certificate, and configure Nginx to use it.# Replace with your actual domain(s)
sudo certbot --nginx -d your_domain -d www.your_domain
Follow the on-screen prompts. When asked, choose the option to redirect HTTP traffic to HTTPS.Part 8: Final VerificationYour application is now fully deployed and secure.1. Check Service StatusVerify that all your services are running correctly.sudo supervisorctl status
You should see RUNNING next to gunicorn, huey, and discord.2. Troubleshooting with LogsIf you encounter any issues, the logs are the first place to look.Gunicorn: /var/log/gunicorn.err.logHuey: /var/log/huey.err.logDiscord: /var/log/discord.err.logNginx: /var/log/nginx/error.logUse tail -f /path/to/logfile to view them in real-time.
