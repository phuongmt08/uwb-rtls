import os
import re
import shutil
import time
import subprocess
import sys
import importlib.util
from models.consts import MAX_VERSIONED_BUILDS
from models.data_models import DfuError

class BuildService:
    _cached_git_hash = None

    @staticmethod
    def get_uwb_project_dir() -> str:
        repo_root = BuildService.get_repo_root()
        return os.path.join(repo_root, "firmware", "uwb")

    @staticmethod
    def get_repo_root() -> str:
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            return os.path.abspath(os.path.join(exe_dir, "..", "..", ".."))
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            return os.path.abspath(os.path.join(script_dir, "..", "..", ".."))

    @staticmethod
    def get_current_git_hash() -> str:
        if BuildService._cached_git_hash:
            return BuildService._cached_git_hash
        try:
            # Hide console window on Windows
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW

            output = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=BuildService.get_repo_root(),
                text=True,
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags
            )
            val = output.strip()
            if val:
                BuildService._cached_git_hash = val
                return val
            return "nogit"
        except Exception:
            return "nogit"



    @staticmethod
    def update_version(auto_inc: bool, log_callback) -> bool:
        try:
            tool_path = os.path.join(BuildService.get_uwb_project_dir(), "tools", "update_version.py")
            if not os.path.exists(tool_path):
                log_callback(f"[VERSION] ERROR: Missing {tool_path}")
                return False, ""

            spec = importlib.util.spec_from_file_location("uwb_update_version_tool", tool_path)
            if spec is None or spec.loader is None:
                log_callback(f"[VERSION] ERROR: Cannot load {tool_path}")
                return False, ""

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.update_version_file(BuildService.get_uwb_project_dir(), auto_inc, log_callback)
        except Exception as e:
            log_callback(f"[VERSION] ERROR: {e}")
            return False, ""

    @staticmethod
    def resolve_make_command() -> str:
        for cmd in ("make", "mingw32-make"):
            if shutil.which(cmd):
                return cmd
        raise DfuError("Cannot find 'make' in PATH. Install Make (or mingw32-make) and try again.")

    @staticmethod
    def run_make_target(target: str, opt_flag: str, log_callback):
        uwb_dir = BuildService.get_uwb_project_dir()
        makefile_path = os.path.join(uwb_dir, "Makefile")
        if not os.path.exists(makefile_path):
            raise DfuError(f"Missing Makefile: {makefile_path}")

        make_cmd = BuildService.resolve_make_command()
        cmd = [make_cmd, "-f", "Makefile", f'OPTFLAGS="{opt_flag} -g3"', target]

        log_callback(f"Running build command: {' '.join(cmd)}")
        cmd_str = " ".join(cmd)
        # Hide console window on Windows
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            cmd_str,
            cwd=uwb_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
            creationflags=creation_flags
        )


        assert process.stdout is not None
        for line in process.stdout:
            log_callback(line.rstrip())

        process.wait()
        if process.returncode != 0:
            raise DfuError(f"Build failed with exit code {process.returncode}")




    @staticmethod
    def archive_versioned_build(log_callback, version_str="1.0.0.0"):
        uwb_dir = BuildService.get_uwb_project_dir()
        build_dir = os.path.join(uwb_dir, "build")
        version_dir = os.path.join(uwb_dir, "build_version")
        hex_path = os.path.join(build_dir, "uwb-rtls.hex")
        map_path = os.path.join(build_dir, "uwb-rtls.map")

        os.makedirs(version_dir, exist_ok=True)

        if not os.path.exists(hex_path):
            raise DfuError(f"Missing build HEX output: {hex_path}")
        if not os.path.exists(map_path):
            raise DfuError(f"Missing build MAP output: {map_path}")

        git_hash = BuildService.get_current_git_hash()
        timestamp = time.strftime("%H%M%S")
        # Accept both old and new patterns for cleanup
        pattern = re.compile(r"^uwb(_|-)rtls_.*\.hex$")

        meta_dir = os.path.join(version_dir, ".metadata")
        os.makedirs(meta_dir, exist_ok=True)


        versioned_base = os.path.join(version_dir, f"uwb_rtls_application_{version_str}_{timestamp}_{git_hash}")
        versioned_hex = versioned_base + ".hex"
        versioned_map = versioned_base + ".map"
        shutil.copy2(hex_path, versioned_hex)
        shutil.copy2(map_path, versioned_map)


        archives = []
        for name in os.listdir(version_dir):
            match = pattern.match(name)
            if not match:
                continue
            archive_hex = os.path.join(version_dir, name)
            try:
                mtime = os.path.getmtime(archive_hex)
            except Exception:
                continue
            archives.append((mtime, archive_hex))

        archives.sort(reverse=True)
        for _, old_hex in archives[MAX_VERSIONED_BUILDS:]:
            old_base = old_hex[:-4]
            old_map = old_base + ".map"
            old_meta = os.path.join(version_dir, ".metadata", os.path.basename(old_base) + "_metadata.json")
            try: os.remove(old_hex)
            except: pass
            if os.path.exists(old_map):
                try: os.remove(old_map)
                except: pass
            if os.path.exists(old_meta):
                try: os.remove(old_meta)
                except: pass


        log_callback(f"Saved versioned HEX/MAP: {os.path.basename(versioned_hex)}")
        return versioned_hex
