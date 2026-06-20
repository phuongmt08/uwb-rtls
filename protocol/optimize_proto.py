import sys
import re

keep_types = {
    'protobuf_addr_t',
    'protobuf_hdr_t',
    'protobuf_version_t',
    'protobuf_none_t',
    'protobuf_ack_t',
    'protobuf_device_information_get_t',
    'protobuf_device_information_resp_t',
    'protobuf_time_sync_get_t',
    'protobuf_time_sync_set_t',
    'protobuf_time_sync_resp_t',
    'protobuf_time_sync_adv_set_t',
    'protobuf_device_reset_t',
    'protobuf_enter_to_bootloader_t',
    'protobuf_flash_erase_t',
    'protobuf_flash_read_t',
    'protobuf_flash_data_t',
    'protobuf_flash_write_t',
    'protobuf_flash_verify_t',
    'protobuf_ble_status_resp_t',
    'protobuf_log_data_t',
    'protobuf_log_clear_t',
    'protobuf_fota_state_resp_t',
    'protobuf_end_session_t',
    'protobuf_packet_t'
}

keep_fields = {
    'hdr', 'none', 'ack', 'device_information_get', 'device_information_resp',
    'time_sync_get', 'time_sync_set', 'time_sync_resp', 'time_sync_adv_set',
    'device_reset', 'flash_erase', 'flash_read', 'flash_data', 'flash_write',
    'ble_status_resp', 'log_data', 'log_clear', 'flash_verify',
    'fota_state_resp', 'enter_to_bootloader', 'end_session'
}

def should_keep_type(typename):
    # Check if typename starts with any kept type
    for kt in keep_types:
        if typename == kt or typename.startswith(kt + '_'):
            return True
    return False

def optimize_header(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    out = []
    i = 0
    in_struct = False
    struct_typename = None
    struct_lines = []

    # State machine to wrap struct definitions
    while i < len(lines):
        line = lines[i]
        
        # Detect start of struct
        # e.g. typedef struct _protobuf_uwb_cfg_t {
        # or e.g. typedef PB_BYTES_ARRAY_T(8) protobuf_uwb_cfg_t_anchor_list_t;
        m_struct_start = re.match(r'typedef\s+struct\s+(_protobuf_\w+)\s*\{', line)
        m_bytes_array = re.match(r'typedef\s+PB_BYTES_ARRAY_T\(\d+\)\s+(\w+);', line)
        
        if m_struct_start and not in_struct:
            in_struct = True
            struct_typename = m_struct_start.group(1)
            # Remove leading underscore from tag struct name if present to match types
            if struct_typename.startswith('_'):
                struct_typename = struct_typename[1:]
            struct_lines = [line]
            i += 1
            continue
        elif m_bytes_array and not in_struct:
            typename = m_bytes_array.group(1)
            if should_keep_type(typename):
                out.append(line)
            else:
                out.append("#ifndef BOOTLOADER\n")
                out.append(line)
                out.append("#endif\n")
            i += 1
            continue

        if in_struct:
            struct_lines.append(line)
            # Detect end of struct
            # e.g. } protobuf_uwb_cfg_t;
            m_struct_end = re.match(r'\}\s*(\w+);', line)
            if m_struct_end:
                typename = m_struct_end.group(1)
                in_struct = False
                if should_keep_type(typename):
                    out.extend(struct_lines)
                else:
                    out.append("#ifndef BOOTLOADER\n")
                    out.extend(struct_lines)
                    out.append("#endif\n")
                struct_lines = []
            i += 1
            continue

        # Outside structs, let's check for union parameters inside protobuf_packet_t
        # union _protobuf_packet_t_params {
        #    ...
        # } params;
        if 'union _protobuf_packet_t_params {' in line:
            out.append(line)
            i += 1
            while i < len(lines):
                line = lines[i]
                if '} params;' in line:
                    out.append(line)
                    break
                
                # We are inside the union. Let's parse each field:
                # e.g. protobuf_sys_config_get_t sys_config_get;
                # e.g. /* Core */ comment
                m_field = re.match(r'\s*(\w+)\s+(\w+);', line)
                if m_field:
                    typename = m_field.group(1)
                    fieldname = m_field.group(2)
                    # We check if this field belongs to the keep list
                    if fieldname in keep_fields:
                        out.append(line)
                    else:
                        out.append("#ifndef BOOTLOADER\n")
                        out.append(line)
                        out.append("#endif\n")
                else:
                    out.append(line)
                i += 1
            i += 1
            continue

        # Check for FIELDLIST macro definition
        # #define protobuf_packet_t_FIELDLIST(X, a) \
        if '#define protobuf_packet_t_FIELDLIST(X, a) \\' in line:
            # We will generate two versions of the macro: one for BOOTLOADER, one for regular
            bootloader_macro = ["#ifdef BOOTLOADER\n", "#define protobuf_packet_t_FIELDLIST(X, a) \\\n"]
            regular_macro = ["#else\n", "#define protobuf_packet_t_FIELDLIST(X, a) \\\n"]
            
            i += 1
            while i < len(lines):
                line = lines[i]
                
                # Check for end of macro (line not ending in \)
                is_end = not line.rstrip('\r\n').endswith('\\')
                
                # Parse FIELDLIST entry
                # e.g. X(a, STATIC, ONEOF, MESSAGE, (params,none,params.none), 2) \
                # or X(a, STATIC, OPTIONAL, MESSAGE, hdr, 1) \
                m_entry = re.search(r'X\(a,\s*\w+,\s*\w+,\s*\w+,\s*(\([^)]+\)|\w+),\s*\d+\)', line)
                if m_entry:
                    field_arg = m_entry.group(1)
                    # If it is ONEOF, it is (params, fieldname, params.fieldname)
                    if field_arg.startswith('('):
                        parts = field_arg.strip('()').split(',')
                        fieldname = parts[1].strip() if len(parts) > 1 else ''
                    else:
                        fieldname = field_arg.strip()
                    
                    if fieldname in keep_fields:
                        bootloader_macro.append(line)
                    else:
                        # For bootloader, if it's not kept, replace trailing backslash with nothing if it was the last line of bootloader macro
                        # (Nanopb macros require backslash on all continuation lines. If we filter them out, we should ensure the last active line in bootloader_macro does not have a backslash, but wait - the last line of the original macro is end_session which is kept, so it naturally has no backslash.)
                        pass
                    regular_macro.append(line)
                else:
                    # Non-entry lines
                    bootloader_macro.append(line)
                    regular_macro.append(line)
                
                if is_end:
                    break
                i += 1
            
            # Clean up the trailing backslash in bootloader_macro if needed
            # (In our case, the last kept field is end_session or rtos_task_stats_resp. Let's find the last X(...) line in bootloader_macro and strip backslash if it is the absolute last entry.)
            # Actually, let's find the last line ending with a backslash and remove it if there are no more entries.
            # But the original end_session_t is at tag 67, and in regular macro, rtos_task_stats_resp is at 74 (last).
            # So in bootloader macro, the last line will be end_session_t, which does NOT have a backslash in the original anyway (Wait, in original, does end_session have a backslash? No, the last line of the macro doesn't have a backslash. But wait, in the original, end_session does have a backslash because rtos resource is after it. So we must strip the backslash from the last line of the bootloader macro!)
            
            # Find the last X(...) line in bootloader_macro
            last_x_idx = -1
            for idx in range(len(bootloader_macro) - 1, -1, -1):
                if 'X(' in bootloader_macro[idx]:
                    last_x_idx = idx
                    break
            if last_x_idx != -1:
                # Strip the backslash
                bootloader_macro[last_x_idx] = bootloader_macro[last_x_idx].rstrip('\r\n\\ ') + '\n'
                
            bootloader_macro.extend(regular_macro)
            bootloader_macro.append("#endif\n")
            out.extend(bootloader_macro)
            i += 1
            continue

        # Check for extern const pb_msgdesc_t declarations
        # e.g. extern const pb_msgdesc_t protobuf_uwb_cfg_t_msg;
        m_extern = re.match(r'extern\s+const\s+pb_msgdesc_t\s+(\w+)_msg;', line)
        if m_extern:
            typename = m_extern.group(1)
            if should_keep_type(typename):
                out.append(line)
            else:
                out.append("#ifndef BOOTLOADER\n")
                out.append(line)
                out.append("#endif\n")
            i += 1
            continue

        # Check for backwards compatibility field defines
        # e.g. #define protobuf_uwb_cfg_t_fields &protobuf_uwb_cfg_t_msg
        m_fields = re.match(r'#define\s+(\w+)_fields\s+&', line)
        if m_fields:
            typename = m_fields.group(1)
            if should_keep_type(typename):
                out.append(line)
            else:
                out.append("#ifndef BOOTLOADER\n")
                out.append(line)
                out.append("#endif\n")
            i += 1
            continue

        out.append(line)
        i += 1

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(out)
    print(f"Header {filepath} optimized successfully.")

def optimize_source(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    out = []
    for line in lines:
        # Check for PB_BIND macros
        # e.g. PB_BIND(protobuf_uwb_cfg_t, protobuf_uwb_cfg_t, AUTO)
        m_bind = re.match(r'PB_BIND\(\s*(\w+)\s*,', line)
        if m_bind:
            typename = m_bind.group(1)
            if should_keep_type(typename):
                out.append(line)
            else:
                out.append("#ifndef BOOTLOADER\n")
                out.append(line)
                out.append("#endif\n")
        else:
            out.append(line)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(out)
    print(f"Source {filepath} optimized successfully.")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python optimize_proto.py <header_path> <source_path>")
        sys.exit(1)
    optimize_header(sys.argv[1])
    optimize_source(sys.argv[2])
