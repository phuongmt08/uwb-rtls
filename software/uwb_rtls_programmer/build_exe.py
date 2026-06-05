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

    # Auto-migrate any existing loose UWB_Programmer_v*.exe files to the legacy folder
    import glob
    legacy_dir = os.path.join(execution_dir, "legacy")
    old_exes = glob.glob(os.path.join(execution_dir, "UWB_Programmer_v*.exe"))
    if old_exes:
        if not os.path.exists(legacy_dir):
            os.makedirs(legacy_dir)
        for old_exe in old_exes:
            dest = os.path.join(legacy_dir, os.path.basename(old_exe))
            print(f"[*] Migrating older version: {old_exe} -> {dest}")
            try:
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(old_exe, dest)
            except Exception as e:
                print(f"[WARN] Could not migrate {old_exe}: {e}")

    # 1. Sync requirements into venv
    print("[*] Syncing dependencies into venv...")
    req_file = os.path.join(venv_path, "..", "requirements.txt")
    subprocess.run([python_exe, "-m", "pip", "install", "-r", req_file], check=True)


    exe_name = "UWB_Programmer"
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
                # Try to read the old version from current_version.txt
                old_version = "unknown"
                version_txt_path = os.path.join(execution_dir, "current_version.txt")
                if os.path.exists(version_txt_path):
                    try:
                        with open(version_txt_path, "r") as f:
                            old_version = f.read().strip()
                    except Exception as e:
                        print(f"[*] Could not read old version text: {e}")
                
                legacy_dir = os.path.join(execution_dir, "legacy")
                if not os.path.exists(legacy_dir):
                    os.makedirs(legacy_dir)
                    
                legacy_exe_path = os.path.join(legacy_dir, f"UWB_Programmer_v{old_version}.exe")
                print(f"[*] Moving existing {output_exe} to legacy: {legacy_exe_path}")
                
                if os.path.exists(legacy_exe_path):
                    try:
                        os.remove(legacy_exe_path)
                    except Exception as e:
                        print(f"[WARN] Could not remove existing legacy EXE: {e}")
                
                try:
                    shutil.move(output_exe, legacy_exe_path)
                except PermissionError:
                    print(f"\n[-] ERROR: Access Denied to {output_exe}")
                    print("[-] Make sure the UWB Programmer application is CLOSED before building.")
                    return
                except Exception as e:
                    print(f"[-] Error moving old EXE to legacy: {e}")
                    return
            
            try:
                shutil.move(dist_file, output_exe)
                print(f"[+] EXE moved to: {output_exe}")
                
                # Write new version to current_version.txt
                version_txt_path = os.path.join(execution_dir, "current_version.txt")
                with open(version_txt_path, "w") as f:
                    f.write(PROGRAMMER_VERSION)
            except Exception as e:
                print(f"[-] Error moving new EXE to output: {e}")
                return
            
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
