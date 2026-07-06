import os
import shutil
import subprocess
import sys
from PIL import Image
from version import __version__

def build():
    app_name = "UWB RTLS Studio"
    print(f"[*] Starting build for {app_name} v{__version__}")
    
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

    # Auto-migrate any existing loose UWB_RTLS_Studio_v*.exe files to the legacy folder
    import glob
    legacy_dir = os.path.join(execution_dir, "legacy")
    old_exes = glob.glob(os.path.join(execution_dir, f"{app_name}_v*.exe"))
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

    # 2. Convert app_icon.png to app_icon.ico using Pillow
    icon_png = os.path.join("resources", "icons", "app_icon.png")
    icon_ico = os.path.join("resources", "icons", "app_icon.ico")
    if os.path.exists(icon_png):
        print(f"[*] Converting {icon_png} to ICO format...")
        try:
            img = Image.open(icon_png)
            # Save as ICO with multiple sizes for Windows Explorer/Taskbar compatibility
            img.save(icon_ico, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
            print(f"[+] Icon saved to: {icon_ico}")
        except Exception as e:
            print(f"[WARN] Could not convert icon to ICO: {e}. Falling back to default or PNG.")
    else:
        print(f"[WARN] app_icon.png not found at {icon_png}. PyInstaller will run without customized icon.")

    output_exe = os.path.join(execution_dir, f"{app_name}.exe")

    # 3. Modify utils/runtime_mode.py temporarily to enforce macro = 0 (UWB_RTLS_TEST_MODE = 0)
    runtime_mode_file = os.path.join("utils", "runtime_mode.py")
    runtime_mode_backup = runtime_mode_file + ".bak"
    
    if os.path.exists(runtime_mode_file):
        print("[*] Backing up runtime_mode.py and forcing UWB_RTLS_TEST_MODE = 0...")
        shutil.copy2(runtime_mode_file, runtime_mode_backup)
        try:
            with open(runtime_mode_file, "r", encoding="utf-8") as f:
                content = f.read()
            # Replace UWB_RTLS_TEST_MODE = 1 with UWB_RTLS_TEST_MODE = 0
            modified_content = content.replace("UWB_RTLS_TEST_MODE = 1", "UWB_RTLS_TEST_MODE = 0")
            with open(runtime_mode_file, "w", encoding="utf-8") as f:
                f.write(modified_content)
        except Exception as e:
            print(f"[WARN] Failed to force UWB_RTLS_TEST_MODE = 0 in code: {e}")

    try:
        # 4. PyInstaller command
        cmd = [
            python_exe, "-m", "PyInstaller",
            "--onefile",
            "--windowed",
            "--noconsole",
            "--paths", "..",
            "--name", app_name,
            "--clean",
            "--add-data", "resources;resources",
            # Exclude unused large modules to minimize EXE size
            "--exclude-module", "PySide6",
            "--exclude-module", "PyQt5",
            "--exclude-module", "PyQt6.QtWebEngineCore",
            "--exclude-module", "PyQt6.QtWebEngineWidgets",
            "--exclude-module", "PyQt6.Qt3DCore",
            "--exclude-module", "PyQt6.QtMultimedia",
            "--exclude-module", "PyQt6.QtBluetooth",
            "--exclude-module", "PyQt6.QtSensors",
            "--exclude-module", "PyQt6.QtCharts",
            "main.py"
        ]

        if os.path.exists(icon_ico):
            cmd.insert(10, "--icon")
            cmd.insert(11, icon_ico)

        print(f"[*] Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        
    finally:
        # 5. Restore utils/runtime_mode.py to original state
        if os.path.exists(runtime_mode_backup):
            print("[*] Restoring original runtime_mode.py...")
            try:
                shutil.move(runtime_mode_backup, runtime_mode_file)
            except Exception as e:
                print(f"[WARN] Could not restore runtime_mode.py: {e}")

    # 6. Post-processing output files
    if result.returncode == 0:
        print("[+] PyInstaller finished successfully.")
        
        # Move output from dist to execution
        dist_file = os.path.join("dist", f"{app_name}.exe")
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
                    
                legacy_exe_path = os.path.join(legacy_dir, f"{app_name}_v{old_version}.exe")
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
                    print(f"[-] Make sure the {app_name} application is CLOSED before building.")
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
                    f.write(__version__)
            except Exception as e:
                print(f"[-] Error moving new EXE to output: {e}")
                return
            
            # Cleanup
            print("[*] Cleaning up build artifacts...")
            if os.path.exists("build"): shutil.rmtree("build")
            if os.path.exists("dist"): shutil.rmtree("dist")
            spec_file = f"{app_name}.spec"
            if os.path.exists(spec_file): os.remove(spec_file)
            
            # Create Desktop Shortcut automatically (Clean name, no .exe extension)
            create_desktop_shortcut(output_exe, icon_ico, app_name.replace("_", " "))
            print("[+] Build complete!")
    else:
        print("[-] PyInstaller failed.")

def create_desktop_shortcut(target_exe, icon_path, shortcut_name):
    """Creates a native Windows Desktop shortcut pointing to the built EXE."""
    import subprocess
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, f"{shortcut_name}.lnk")
        target_exe = os.path.abspath(target_exe)
        working_dir = os.path.dirname(target_exe)
        
        ps_script = f"""
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
        $Shortcut.TargetPath = '{target_exe}'
        $Shortcut.WorkingDirectory = '{working_dir}'
        """
        if icon_path and os.path.exists(icon_path):
            ps_script += f"\n$Shortcut.IconLocation = '{os.path.abspath(icon_path)}'"
        ps_script += "\n$Shortcut.Save()"
        
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, check=True)
        print(f"[+] Automatically created clean Desktop shortcut: {shortcut_path}")
    except Exception as e:
        print(f"[WARN] Could not create desktop shortcut automatically: {e}")

if __name__ == "__main__":
    build()
