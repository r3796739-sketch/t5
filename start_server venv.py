# In start_server venv.py

import subprocess
import sys
import os
import signal

def setup_environment():
    """
    Checks for a virtual environment, creates it if it doesn't exist,
    and installs dependencies from requirements.txt.
    Returns the path to the python executable in the venv.
    """
    venv_dir = "venv"
    is_windows = (os.name == 'nt')
    
    python_executable = os.path.join(venv_dir, 'Scripts', 'python.exe') if is_windows else os.path.join(venv_dir, 'bin', 'python')
    pip_executable = os.path.join(venv_dir, 'Scripts', 'pip.exe') if is_windows else os.path.join(venv_dir, 'bin', 'pip')

    if not os.path.isdir(venv_dir):
        print(f"--- Virtual environment not found. Creating one at './{venv_dir}'... ---")
        try:
            subprocess.run([sys.executable, '-m', 'venv', venv_dir], check=True)
            print("--- Virtual environment created successfully. ---")
        except subprocess.CalledProcessError as e:
            print(f"!!! ERROR: Failed to create virtual environment. {e} !!!")
            sys.exit(1)

    print("--- Installing/updating dependencies from requirements.txt... ---")
    try:
        subprocess.run([pip_executable, 'install', '-r', 'requirements.txt'], check=True)
        print("--- Dependencies installed successfully. ---")
    except subprocess.CalledProcessError as e:
        print(f"!!! ERROR: Failed to install dependencies. {e} !!!")
        sys.exit(1)
        
    return python_executable

def start_web_server(python_executable):
    """Starts the Flask web server."""
    is_windows = (os.name == 'nt')
    
    if is_windows:
        print("--- Windows OS detected. Starting Flask development server. ---")
        server_cmd = [python_executable, 'app.py']
    else:
        print("--- Linux/macOS detected. Starting Gunicorn server. ---")
        gunicorn_executable = os.path.join("venv", 'bin', 'gunicorn')
        server_cmd = [gunicorn_executable, '--workers', '4', '--bind', '0.0.0.0:5000', 'app:app']

    print(f"--- Starting Web Server ---")
    server_process = subprocess.Popen(server_cmd)
    print(f"Web Server started with PID: {server_process.pid}")
    return server_process

def start_huey_worker(python_executable):
    """Starts the Huey background task worker."""
    print("--- Starting Huey Background Worker ---")
    huey_cmd = [python_executable, 'run_worker.py']
    worker_process = subprocess.Popen(huey_cmd)
    print(f"Huey worker started with PID: {worker_process.pid}")
    return worker_process

if __name__ == '__main__':
    python_executable = setup_environment()
    
    server_proc = start_web_server(python_executable)
    worker_proc = start_huey_worker(python_executable)
    
    print("\nWeb Server and Huey Worker are running.")
    print("Press Ctrl+C to stop.")
    
    try:
        # Wait for the server process to complete. If it crashes, the script will exit.
        server_proc.wait()
    except KeyboardInterrupt:
        print("\n--- Shutting down all processes ---")
    finally:
        # Terminate both processes
        worker_proc.terminate()
        server_proc.terminate()
        # Wait for them to exit
        worker_proc.wait()
        server_proc.wait()
        print("--- All processes have been stopped. ---")