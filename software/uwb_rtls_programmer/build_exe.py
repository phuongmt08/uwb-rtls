import os
import shutil
import subprocess
import sys
from version import PROGRAMMER_VERSION

def build():
    print(f"[*] Starting build for UWB Programmer v{PROGRAMMER_VERSION}")
    
    # Locate venv in parent directory
    venv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv"))
    python_exe = os.path.join(venv_path, "Scripts", "python.exe")
    
    if not os.path.exists(python_exe):
        print(f"[-] Error: Could not find venv at {venv_path}. Please create a .venv in software/ folder.")
        return

    print(f"[*] Using VENV Python: {python_exe}")
    
    execution_dir = "execution"
    if not os.path.exists(execution_dir):
        os.makedirs(execution_dir)

    # 1. Sync requirements into venv
    print("[*] Syncing dependencies into venv...")
    req_file = os.path.join(venv_path, "..", "requirements.txt")
    subprocess.run([python_exe, "-m", "pip", "install", "-r", req_file], check=True)


    exe_name = f"UWB_Programmer_v{PROGRAMMER_VERSION}"
    output_exe = os.path.join(execution_dir, f"{exe_name}.exe")
    
    # 2. PyInstaller command using venv's python
    cmd = [
        python_exe, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--noconsole",
        "--paths", "..",
        "--name", exe_name,
        "--clean",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.Qt3DRender",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtBluetooth",
        "--exclude-module", "PySide6.QtSensors",
        "--exclude-module", "PySide6.QtCharts",
        "main.py"
    ]

    
    print(f"[*] Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("[+] PyInstaller finished successfully.")
        
        # Move output from dist to execution
        dist_file = os.path.join("dist", f"{exe_name}.exe")
        if os.path.exists(dist_file):
            if os.path.exists(output_exe):
                print(f"[*] Version {PROGRAMMER_VERSION} already exists. Attempting to replace old EXE.")
                try:
                    os.remove(output_exe)
                except PermissionError:
                    print(f"\n[-] ERROR: Access Denied to {output_exe}")
                    print("[-] Make sure the UWB Programmer application is CLOSED before building.")
                    return
                except Exception as e:
                    print(f"[-] Error removing old EXE: {e}")
                    return
            
            shutil.move(dist_file, output_exe)

            print(f"[+] EXE moved to: {output_exe}")
            
            # Cleanup
            print("[*] Cleaning up build artifacts...")
            if os.path.exists("build"): shutil.rmtree("build")
            if os.path.exists("dist"): shutil.rmtree("dist")
            spec_file = f"{exe_name}.spec"
            if os.path.exists(spec_file): os.remove(spec_file)
    else:
        print("[-] PyInstaller failed.")


if __name__ == "__main__":
    build()
