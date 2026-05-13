"""Generate C header for CMIS Low Memory with Bit-Fields, Unions, and Enums."""

import re

def sanitize_name(name):
    """Sanitize a field name for C."""
    # Fix PDF extraction artifacts: single letter + space + word -> joined
    name = re.sub(r'\b([A-Z])\s+([a-z])', r'\1\2', name)
    # Remove footnote/note markers: trailing digits/bracketed refs with optional commas
    name = re.sub(r'\s+\[\d+(?:,\s*\d+)*\]$', '', name)
    name = re.sub(r'\s+\d+(?:,\s*\d+)*$', '', name)
    clean = ""
    cap_next = False
    for c in name:
        if c == ' ':
            cap_next = True
        elif c.isalnum():
            if cap_next and not clean:
                clean += c.upper() if c.isalpha() else c
            elif cap_next:
                clean += c.upper() if c.isalpha() else ('_' + c)
            else:
                clean += c
            cap_next = False
        elif c == '_':
            clean += '_'
            cap_next = False
        else:
            if clean and clean[-1] != '_':
                clean += '_'
            cap_next = False
    clean = clean.strip('_')
    if not clean:
        return "unnamed"
    if clean.lower() in ['register', 'volatile', 'typedef', 'struct', 'enum']:
        clean += "_reg"
    return clean

def clean_c_string(text):
    """Clean up PDF text for C comments."""
    if not text:
        return ""
    text = " ".join(text.split())
    return text

def format_doxygen(brief, details, access, indent_level=4, width=120):
    """Generate Doxygen-style comment block."""
    lines = []
    has_content = brief or details or access
    
    if not has_content:
        return lines

    indent_str = " " * indent_level
    lines.append(f"{indent_str}/**")
    
    if brief:
        lines.append(f"{indent_str} * @brief {brief}")
    
    if details:
        words = details.split()
        current_line = f"{indent_str} * @details "
        for word in words:
            if len(current_line) + len(word) + 1 > width:
                lines.append(current_line)
                current_line = f"{indent_str} * " + word
            else:
                current_line += word + " "
        if current_line.strip() != f"{indent_str} *":
            lines.append(current_line.rstrip())
            
    if access:
        lines.append(f"{indent_str} * @access {access}")
        
    lines.append(f"{indent_str} */")
    return lines

def parse_bits(bits_str):
    """Parse bits string like '7-4', '3', '5-0' into (msb, lsb)."""
    if not bits_str:
        return (7, 0)
    if bits_str.lower() == 'all':
        return (7, 0)
    if '-' in bits_str:
        try:
            msb, lsb = bits_str.split('-')
            return (int(msb), int(lsb))
        except:
            pass
    try:
        val = int(bits_str)
        return (val, val)
    except:
        pass
    return (7, 0)


def _build_byte_map(registers, byte_start, byte_end):
    """Build byte-to-registers map within the given byte range."""
    byte_map = {}
    for reg in registers:
        byte_str = reg.get('Byte', '')
        name = reg.get('Field Name') or reg.get('Name') or reg.get('Register Name', '')
        if not byte_str or (not name or name == '-') and not reg.get('_synthetic'):
            continue
        try:
            if '-' in str(byte_str):
                parts = str(byte_str).split('-')
                start = int(parts[0])
                width = int(parts[1]) - start + 1
                if byte_start <= start < byte_end:
                    reg['width_bytes'] = width
                    if start not in byte_map:
                        byte_map[start] = []
                    # Dedup: skip if same name already at this start byte
                    existing_names = {r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                                      for r in byte_map[start]}
                    if name in existing_names:
                        continue
                    # Synthetic entries take priority: remove non-synthetic if synthetic present
                    if reg.get('_synthetic'):
                        byte_map[start] = [r for r in byte_map[start] if r.get('_synthetic')]
                    elif any(r.get('_synthetic') for r in byte_map[start]):
                        continue
                    byte_map[start].append(reg)
                    # Sort by width ascending: detail entries (narrower) come first
                    byte_map[start].sort(key=lambda r: r.get('width_bytes', 1))
                    # If both overview and detail entries exist, drop overview entries
                    has_detail = any(not r.get('_overview') for r in byte_map[start])
                    if has_detail:
                        byte_map[start] = [r for r in byte_map[start] if not r.get('_overview')]
            else:
                b = int(byte_str)
                if byte_start <= b < byte_end:
                    if b not in byte_map:
                        byte_map[b] = []
                    reg['width_bytes'] = 1
                    existing_names = {r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                                      for r in byte_map[b]}
                    if name in existing_names:
                        continue
                    # Synthetic entries take priority
                    if reg.get('_synthetic'):
                        byte_map[b] = [r for r in byte_map[b] if r.get('_synthetic')]
                    elif any(r.get('_synthetic') for r in byte_map[b]):
                        continue
                    byte_map[b].append(reg)
                    byte_map[b].sort(key=lambda r: r.get('width_bytes', 1))
                    # If both overview and detail entries exist, drop overview entries
                    has_detail = any(not r.get('_overview') for r in byte_map[b])
                    if has_detail:
                        byte_map[b] = [r for r in byte_map[b] if not r.get('_overview')]
        except (ValueError, TypeError):
            continue
    return byte_map


def _build_struct_lines(registers, byte_start, byte_end, struct_name, header_comment, guard_name):
    """Build the C struct definition lines.

    Returns list of code lines (not joined, not written to file).
    """
    num_bytes = byte_end - byte_start

    lines = [
        f"/* Auto-generated by CMIS2C - {header_comment} */",
        f"#ifndef {guard_name}",
        f"#define {guard_name}",
        "",
        "#include <stdint.h>",
        ""
    ]

    # Collect and emit enums (dedup by table_id)
    field_enums = {}
    for reg in registers:
        if 'enum' in reg:
            enum_data = reg['enum']
            tid = enum_data.get('table_id', id(enum_data))
            if tid not in field_enums:
                name = reg.get('Field Name') or reg.get('Name') or reg.get('Register Name', '')
                clean_name = sanitize_name(name)
                field_enums[tid] = (clean_name, enum_data)

    for tid, (field_name, enum_data) in field_enums.items():
        # Strip trailing digits from enum type name (e.g. DPStateHostLane2 -> DPStateHostLane)
        enum_name = f"Enums_{sanitize_name(re.sub(r'\d+$', '', field_name))}"
        lines.append("typedef enum {")
        for code, enum_info in sorted(enum_data['values'].items()):
            enum_name_raw = enum_info['name']
            if not enum_name_raw or enum_name_raw == '-':
                enum_val_name = f"Reserved_{code}"
            else:
                enum_val_name = sanitize_name(enum_name_raw)
            comment = enum_info['name'] if enum_info['name'] != enum_info['desc'] else enum_info['desc']
            lines.append(f"    {enum_name}_{enum_val_name} = {code},  /**< {clean_c_string(comment)} **/")
        lines.append(f"}} {enum_name};")
        lines.append("")

    byte_map = _build_byte_map(registers, byte_start, byte_end)

    def _try_wide_register(start_byte):
        """If start_byte begins a group of N bytes with identical field pattern,
        return (N, base_name, int_type). Otherwise None."""
        regs0 = byte_map.get(start_byte, [])
        if not regs0:
            return None
        first_bits = regs0[0].get('Bits') or regs0[0].get('Bit', '')
        fw_msb, fw_lsb = parse_bits(first_bits)
        field_w = fw_msb - fw_lsb + 1
        if field_w == 8:
            return None
        fields0 = [r for r in regs0 if (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) and (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) != '-']
        n_per_byte = len(fields0)
        if n_per_byte * field_w != 8:
            return None
        base_names = set()
        for f in fields0:
            n = f.get('Field Name','') or f.get('Name','') or f.get('Register Name','')
            base_names.add(re.sub(r'\d+$', '', n))
        group_bytes = 0
        for i in range(8):
            bi = start_byte + i
            if bi not in byte_map:
                break
            fields_i = [r for r in byte_map[bi] if (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) and (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) != '-']
            if len(fields_i) != n_per_byte:
                break
            names_i = set()
            for r in fields_i:
                n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                b = r.get('Bits') or r.get('Bit','')
                m, l = parse_bits(b)
                if m - l + 1 != field_w:
                    break
                names_i.add(re.sub(r'\d+$', '', n))
            if names_i != base_names:
                break
            group_bytes += 1
        if group_bytes < 2:
            return None
        total_bits = group_bytes * 8
        if total_bits == 32:
            int_type = 'uint32_t'
        elif total_bits == 16:
            int_type = 'uint16_t'
        else:
            return None
        base_name = sanitize_name(list(base_names)[0])
        return (group_bytes, base_name, int_type)

    lines.append(f"/** @brief {header_comment} (Bytes {byte_start}-{byte_end-1}) */")
    lines.append("typedef struct __attribute__((packed)) {")
    lines.append("")

    reserved_start = -1
    reserved_len = 0
    consumed_bytes = set()
    used_names = set()

    def unique_name(name, byte_offset):
        """Ensure field name is unique within the struct."""
        if name not in used_names:
            used_names.add(name)
            return name
        candidate = f"{name}_{byte_offset}"
        used_names.add(candidate)
        return candidate

    def _derive_accessor(fields_list, byte_offset):
        """Derive a union byte-accessor name from field names.
        Only rename when ALL fields share the same stem (differing only by trailing digits).
        Otherwise fall back to r[offset]."""
        if not fields_list:
            return f"r{byte_offset}"
        names = [f['name'] for f in fields_list]
        stripped = [re.sub(r'\d+$', '', n).strip('_') for n in names]
        if len(set(stripped)) == 1 and stripped[0]:
            return unique_name(stripped[0], byte_offset)
        return f"r{byte_offset}"

    def flush_reserved():
        nonlocal reserved_start, reserved_len
        if reserved_len > 0:
            if reserved_len == 1:
                lines.append(f"    uint8_t reserved_{reserved_start};  /**< r{reserved_start} Reserved */")
            else:
                lines.append(f"    uint8_t reserved_{reserved_start}[{reserved_len}];  /**< r{reserved_start}.. */")
        reserved_len = 0
        reserved_start = -1

    # Detect array starts
    app_array_start = None
    app9_array_start = None
    cdb_array_start = None
    mlao_array_start = None
    scs_array_start = None
    wide_regs = {}  # start_byte -> (group_bytes, base_name, int_type)
    lane_arrays = {}  # start_byte -> byte_count (for 8-lane repeated structures)

    for b in range(byte_start, byte_end):
        if b not in byte_map:
            continue

        regs_at_byte = byte_map[b]

        def _name_has(entries, substr):
            for r in entries:
                n = r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                if substr in n:
                    return True
            return False

        first_reg = regs_at_byte[0]
        name0 = first_reg.get('Field Name', '') or first_reg.get('Name', '') or first_reg.get('Register Name', '')

        if app_array_start is None and _name_has(regs_at_byte, 'App1'):
            if (b + 4) in byte_map:
                if _name_has(byte_map[b + 4], 'App2'):
                    app_array_start = b
                    for k in range(32):
                        consumed_bytes.add(b + k)

        if app9_array_start is None and _name_has(regs_at_byte, 'App9'):
            if (b + 4) in byte_map:
                if _name_has(byte_map[b + 4], 'App10'):
                    app9_array_start = b
                    for k in range(28):
                        consumed_bytes.add(b + k)

        if mlao_array_start is None and _name_has(regs_at_byte, 'MediaLaneAssignmentOptionsApp1'):
            if (b + 1) in byte_map:
                if _name_has(byte_map[b + 1], 'MediaLaneAssignmentOptionsApp2'):
                    mlao_array_start = b
                    for k in range(15):
                        consumed_bytes.add(b + k)

        # Detect Staged Control Sets (35 bytes each, mirrored at r143 and r178)
        if scs_array_start is None and _name_has(regs_at_byte, 'ApplyDPInitLane'):
            scs1 = b + 35
            if scs1 in byte_map and _name_has(byte_map[scs1], 'ApplyDPInitLane'):
                scs_array_start = b
                for k in range(70):
                    consumed_bytes.add(b + k)

        # Detect wide registers (multi-byte groups with identical field patterns)
        # Detect lane array: N consecutive bytes with same field pattern (e.g. monitors, DPConfig)
        if b not in consumed_bytes:
            regs0 = byte_map[b]
            first_names = []
            for r in regs0:
                n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                if n and n != '-':
                    first_names.append((re.sub(r'\d+$', '', n), n))
            if first_names:
                wb0 = regs0[0].get('width_bytes', 1)
                # Skip lane arrays for simple 1-byte full-width fields (let them be individual)
                first_bits = regs0[0].get('Bits') or regs0[0].get('Bit', '')
                fw_msb, fw_lsb = parse_bits(first_bits)
                if wb0 == 1 and fw_msb - fw_lsb + 1 == 8 and len(first_names) == 1:
                    # Skip arrayization for generic single-byte fields, but allow known arrays
                    stems0 = set(s for s, _ in first_names)
                    if not any('HostScratchPad' in s for s in stems0):
                        first_names = []
            if first_names:
                wb0 = regs0[0].get('width_bytes', 1)
                stride = wb0 if wb0 > 1 else 1
                count = 8  # always 8 lanes
                if all((b + i * stride) in byte_map for i in range(count)):
                    all_match = True
                    for i in range(1, count):
                        ni = byte_map[b + i * stride]
                        i_stems = set()
                        for r in ni:
                            n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                            if n and n != '-':
                                i_stems.add(sanitize_name(re.sub(r'\d+$', '', n)))
                        f_stems = set(sanitize_name(s) for s, _ in first_names)
                        if i_stems != f_stems:
                            all_match = False
                            break
                    if all_match and b not in lane_arrays:
                        lane_arrays[b] = count
                        for i in range(count):
                            for k in range(stride):
                                consumed_bytes.add(b + i * stride + k)

        if b not in consumed_bytes:
            wr = _try_wide_register(b)
            if wr and b not in wide_regs:
                group_bytes, base_name, int_type = wr
                wide_regs[b] = (group_bytes, base_name, int_type)
                for k in range(group_bytes):
                    consumed_bytes.add(b + k)

        width_bytes = first_reg.get('width_bytes', 1)
        if width_bytes > 1:
            for k in range(1, width_bytes):
                consumed_bytes.add(b + k)

    for b in range(byte_start, byte_end):
        if b in consumed_bytes:
            flush_reserved()
            if b == app_array_start:
                lines.append("    /** @brief Application Descriptors (AppSel 1-8) */")
                lines.append("    struct {")
                for offset in range(4):
                    byte_idx = b + offset
                    if byte_idx in byte_map:
                        regs = byte_map[byte_idx]
                        fields = []
                        for r in regs:
                            name = r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                            if name and name != '-':
                                clean = re.sub(r'App\d+', '', name).strip('_')
                                bits = r.get('Bits') or r.get('Bit', '')
                                desc = clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', ''))))
                                rtype = clean_c_string(r.get('Type', ''))
                                msb, lsb = parse_bits(bits)
                                fields.append({'name': clean, 'width': msb - lsb + 1, 'lsb': lsb, 'desc': desc, 'type': rtype})
                        
                        fields.sort(key=lambda x: x['lsb'])
                        current_bit = 0
                        for f in fields:
                            gap = f['lsb'] - current_bit
                            if gap > 0:
                                lines.append(f"        uint8_t _pad_{offset}_{current_bit} : {gap};")
                            lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=8))
                            msb_bit = f['lsb'] + f['width'] - 1
                            if f['width'] == 1:
                                bit_note = f"r{byte_idx}.{f['lsb']}"
                            else:
                                bit_note = f"r{byte_idx}.{msb_bit}-{f['lsb']}"
                            lines.append(f"        uint8_t {f['name']} : {f['width']};  /* {bit_note} */")
                            current_bit = f['lsb'] + f['width']
                        if current_bit < 8:
                            lines.append(f"        uint8_t _pad_{offset}_{current_bit} : {8 - current_bit};")
                    else:
                        lines.append(f"        uint8_t reserved_{offset};")
                lines.append("    } AppDescriptors[8];")

            elif b == app9_array_start:
                lines.append("    /** @brief Application Descriptors (AppSel 9-15) */")
                lines.append("    struct {")
                for offset in range(4):
                    byte_idx = b + offset
                    if byte_idx in byte_map:
                        regs = byte_map[byte_idx]
                        fields = []
                        for r in regs:
                            name = r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                            if name and name != '-':
                                clean = re.sub(r'App\d+', '', name).strip('_')
                                bits = r.get('Bits') or r.get('Bit', '')
                                desc = clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', ''))))
                                rtype = clean_c_string(r.get('Type', ''))
                                msb, lsb = parse_bits(bits)
                                fields.append({'name': clean, 'width': msb - lsb + 1, 'lsb': lsb, 'desc': desc, 'type': rtype})

                        fields.sort(key=lambda x: x['lsb'])
                        current_bit = 0
                        for f in fields:
                            gap = f['lsb'] - current_bit
                            if gap > 0:
                                lines.append(f"        uint8_t _pad_{offset}_{current_bit} : {gap};")
                            lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=8))
                            msb_bit = f['lsb'] + f['width'] - 1
                            if f['width'] == 1:
                                bit_note = f"r{byte_idx}.{f['lsb']}"
                            else:
                                bit_note = f"r{byte_idx}.{msb_bit}-{f['lsb']}"
                            lines.append(f"        uint8_t {f['name']} : {f['width']};  /* {bit_note} */")
                            current_bit = f['lsb'] + f['width']
                        if current_bit < 8:
                            lines.append(f"        uint8_t _pad_{offset}_{current_bit} : {8 - current_bit};")
                    else:
                        lines.append(f"        uint8_t reserved_{offset};")
                lines.append("    } AppDescriptors[7];")

            elif b == mlao_array_start:
                lines.append("    /** @brief Media Lane Assignment Options (App 1-15) */")
                lines.append("    uint8_t MediaLaneAssignmentOptions[15];")

            elif b == scs_array_start:
                def _scs_emit_byte(regs_list, byte_idx, indent):
                    """Emit one byte as union with bit-fields inside SCS."""
                    fields = []
                    for r in regs_list:
                        name = r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                        if name and name != '-':
                            clean = sanitize_name(name)
                            bits = r.get('Bits') or r.get('Bit', '')
                            desc = clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', ''))))
                            rtype = clean_c_string(r.get('Type', ''))
                            msb, lsb = parse_bits(bits)
                            fields.append({'name': clean, 'width': msb - lsb + 1, 'lsb': lsb, 'desc': desc, 'type': rtype})
                    if not fields:
                        lines.append(f"{indent}    uint8_t reserved_{byte_idx};")
                        return
                    fields.sort(key=lambda x: x['lsb'])
                    # Derive accessor name (only when all fields share the same stem)
                    names = [f['name'] for f in fields]
                    stripped = [re.sub(r'\d+$', '', n).strip('_') for n in names]
                    if len(set(stripped)) == 1 and stripped[0]:
                        acc = stripped[0]
                    else:
                        acc = f"r{byte_idx}"
                    if acc in scs_used_names:
                        acc = f"{acc}_{byte_idx}"
                    scs_used_names.add(acc)
                    # Emit doxygen for each field
                    for f in fields:
                        lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=len(indent) + 4))
                    if len(fields) == 1 and fields[0]['width'] == 8:
                        lines.append(f"{indent}    uint8_t {acc};  /* r{byte_idx} */")
                    else:
                        lines.append(f"{indent}    union {{")
                        lines.append(f"{indent}        struct {{")
                        current_bit = 0
                        for f in fields:
                            gap = f['lsb'] - current_bit
                            if gap > 0:
                                lines.append(f"{indent}            uint8_t _pad_{byte_idx}_{current_bit} : {gap};")
                            msb_bit = f['lsb'] + f['width'] - 1
                            if f['width'] == 1:
                                bit_note = f"r{byte_idx}.{f['lsb']}"
                            else:
                                bit_note = f"r{byte_idx}.{msb_bit}-{f['lsb']}"
                            lines.append(f"{indent}            uint8_t {f['name']} : {f['width']};  /* {bit_note} */")
                            current_bit = f['lsb'] + f['width']
                        if current_bit < 8:
                            lines.append(f"{indent}            uint8_t _pad_{byte_idx}_{current_bit} : {8 - current_bit};")
                        lines.append(f"{indent}        }};")
                        lines.append(f"{indent}        uint8_t {acc};")
                        lines.append(f"{indent}    }};")

                scs_used_names = set()
                lines.append("    /** @brief Staged Control Set (SCS0 @ r143, SCS1 @ r178) */")
                lines.append("    struct __attribute__((packed)) {")
                offset = 0
                while offset < 35:
                    byte_idx = b + offset
                    if byte_idx in byte_map:
                        # r145-152: DataPathConfig per lane
                        if offset == 2 and (byte_idx + 1) in byte_map:
                            cur_n = byte_map[byte_idx][0].get('Field Name', '') or byte_map[byte_idx][0].get('Name', '')
                            next_n = byte_map[byte_idx+1][0].get('Field Name', '') or byte_map[byte_idx+1][0].get('Name', '')
                            if cur_n and next_n and cur_n[:-1] == next_n[:-1]:
                                lines.append("        /** @brief Data Path Configuration per lane */")
                                lines.append("        struct __attribute__((packed)) {")
                                _scs_emit_byte(byte_map[byte_idx], byte_idx, "        ")
                                lines.append("        } DataPathConfig[8];")
                                offset += 8
                                continue
                        # Detect wide registers: consecutive bytes with same field structure
                        wr = _try_wide_register(byte_idx)
                        if wr:
                            group_bytes, base_name, int_type = wr
                            lines.append(f"        /** @brief {base_name} per lane ({group_bytes*8}-bit register) */")
                            lines.append(f"        union {{")
                            lines.append(f"            struct __attribute__((packed)) {{")
                            # Collect all fields, sort by LSB for correct little-endian bit allocation
                            all_fields = []
                            for bo in range(group_bytes):
                                bi = byte_idx + bo
                                for r in byte_map[bi]:
                                    n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                                    if n and n != '-':
                                        msb, lsb = parse_bits(r.get('Bits') or r.get('Bit', ''))
                                        gb = bo * 8 + lsb
                                        w = msb - lsb + 1
                                        all_fields.append((gb, sanitize_name(n), w,
                                            clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', '')))),
                                            clean_c_string(r.get('Type', ''))))
                            all_fields.sort(key=lambda x: x[0])  # sort by LSB
                            for gb, cn, w, desc, rtype in all_fields:
                                lines.extend(format_doxygen(cn, desc, rtype, indent_level=16))
                                lines.append(f"                {int_type} {cn} : {w};  /* bits {gb+w-1}:{gb} */")
                            lines.append(f"            }};")
                            lines.append(f"            {int_type} {base_name};")
                            lines.append(f"        }};")
                            offset += group_bytes
                            continue
                        _scs_emit_byte(byte_map[byte_idx], byte_idx, "    ")
                    else:
                        lines.append(f"        uint8_t reserved_{offset};")
                    offset += 1
                lines.append("    } StagedControlSet[2];")

            elif b in wide_regs:
                group_bytes, base_name, int_type = wide_regs[b]
                lines.append(f"    /** @brief {base_name} ({int_type}, {group_bytes} bytes) */")
                lines.append(f"    union {{")
                lines.append(f"        struct __attribute__((packed)) {{")
                all_fields = []
                for bo in range(group_bytes):
                    bi = b + bo
                    for r in byte_map[bi]:
                        n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                        if n and n != '-':
                            msb, lsb = parse_bits(r.get('Bits') or r.get('Bit', ''))
                            gb = bo * 8 + lsb
                            w = msb - lsb + 1
                            all_fields.append((gb, sanitize_name(n), w,
                                clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', '')))),
                                clean_c_string(r.get('Type', ''))))
                all_fields.sort(key=lambda x: x[0])
                for gb, cn, w, desc, rtype in all_fields:
                    cn = unique_name(cn, b)
                    lines.extend(format_doxygen(cn, desc, rtype, indent_level=8))
                    lines.append(f"        {int_type} {cn} : {w};  /* bits {gb+w-1}:{gb} */")
                lines.append(f"        }};")
                lines.append(f"        {int_type} {unique_name(base_name, b)};")
                lines.append(f"    }};")
            elif b in lane_arrays:
                count = lane_arrays[b]
                regs0 = byte_map[b]
                wb0 = regs0[0].get('width_bytes', 1)
                field_count = len([r for r in regs0 if (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) and (r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')) != '-'])
                if field_count >= 2:
                    # Multi-field struct per element
                    fields = []
                    for r in regs0:
                        n = r.get('Field Name','') or r.get('Name','') or r.get('Register Name','')
                        if n and n != '-':
                            cn = sanitize_name(re.sub(r'\d+$', '', n))
                            msb, lsb = parse_bits(r.get('Bits') or r.get('Bit',''))
                            w = msb - lsb + 1
                            desc = clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', ''))))
                            rtype = clean_c_string(r.get('Type', ''))
                            fields.append({'name': cn, 'width': w, 'lsb': lsb, 'desc': desc, 'type': rtype})
                    fields.sort(key=lambda x: x['lsb'])
                    # Use r[offset] naming when fields don't share a common stem,
                    # except for known patterns
                    stems = set(f['name'] for f in fields)
                    if any('ExplicitControl' in s or 'AppSelCode' in s for s in stems):
                        base_name = 'DPConfigLane'
                    elif any('GridSpacing' in s for s in stems):
                        base_name = 'GridSpacing'
                    elif any('WavelengthUnlockStatus' in s or 'TuningInProgress' in s for s in stems):
                        base_name = 'StatusIndicator'
                    elif any('TargetOutputPowerOORFlag' in s or 'TuningCompleteFlag' in s for s in stems):
                        base_name = 'Flag'
                    elif any('TargetOutputPowerOORMask' in s or 'TuningCompleteMask' in s for s in stems):
                        base_name = 'Mask'
                    elif len(stems) == 1:
                        base_name = unique_name(fields[0]['name'], b)
                    else:
                        base_name = f"r{b}"
                    lines.append(f"    /** @brief {base_name} per lane */")
                    lines.append(f"    struct __attribute__((packed)) {{")
                    lines.append(f"        union {{")
                    lines.append(f"            struct {{")
                    current_bit = 0
                    for f in fields:
                        gap = f['lsb'] - current_bit
                        if gap > 0:
                            lines.append(f"                    uint8_t _pad_{b}_{current_bit} : {gap};")
                        lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=16))
                        lines.append(f"                uint8_t {f['name']} : {f['width']};")
                        current_bit = f['lsb'] + f['width']
                    if current_bit < 8:
                        lines.append(f"                    uint8_t _pad_{b}_{current_bit} : {8 - current_bit};")
                    lines.append(f"            }};")
                    lines.append(f"            uint8_t rval;")
                    lines.append(f"        }};")
                    lines.append(f"    }} {unique_name(base_name, b)}[{count}];")
                else:
                    n0 = regs0[0].get('Field Name','') or regs0[0].get('Name','') or regs0[0].get('Register Name','')
                    base = sanitize_name(re.sub(r'\d+$', '', n0))
                    if wb0 == 4:
                        ctype = 'uint32_t'
                    elif wb0 == 2:
                        ctype = 'uint16_t'
                    else:
                        ctype = 'uint8_t'
                    desc = clean_c_string(regs0[0].get('Field Description', regs0[0].get('Register Description', '')))
                    lines.extend(format_doxygen(base, desc, '', indent_level=4))
                    lines.append(f"    {ctype} {base}[{count}];")
            elif b == cdb_array_start:
                lines.append("    /** @brief CDB Status (Instance 1 & 2) */")
                lines.append("    struct {")
                struct_lines = []
                fields = [
                    {'name': 'CdbCommandResult', 'width': 6, 'lsb': 0,
                     'desc': 'The CdbCommandResult field provides more detailed classification for each of the three coarse query results encoded by Bit 7 (CdbIsBusy) and Bit 6 (CdbHasFailed).',
                     'type': 'RO'},
                    {'name': 'CdbHasFailed', 'width': 1, 'lsb': 6,
                     'desc': 'Bool: CdbHasFailed bit indicates if there was a failure, after the module has completed execution of the last CDB command.',
                     'type': 'RO'},
                    {'name': 'CdbIsBusy', 'width': 1, 'lsb': 7,
                     'desc': 'Bool: CdbIsBusy status bit indicates whether the module is still busy, or idle and ready to accept a new CDB command.',
                     'type': 'RO'}
                ]
                fields.sort(key=lambda x: x['lsb'])
                current_bit = 0
                for f in fields:
                    gap = f['lsb'] - current_bit
                    if gap > 0:
                        pad_name = f"_pad_{b}_{current_bit}"
                        struct_lines.append(f"            uint8_t {pad_name} : {gap};")
                    lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=12))
                    msb_bit = f['lsb'] + f['width'] - 1
                    if f['width'] == 1:
                        bit_note = f"r{b}.{f['lsb']}"
                    else:
                        bit_note = f"r{b}.{msb_bit}-{f['lsb']}"
                    struct_lines.append(f"            uint8_t {f['name']} : {f['width']};  /* {bit_note} */")
                    current_bit = f['lsb'] + f['width']
                if current_bit < 8:
                    pad_name = f"_pad_{b}_{current_bit}"
                    struct_lines.append(f"            uint8_t {pad_name} : {8 - current_bit};")

                lines.append(f"    union {{")
                lines.append(f"        struct {{")
                for line in struct_lines:
                    lines.append(line)
                lines.append(f"        }};")
                lines.append(f"        uint8_t r{b};")
                lines.append(f"    }};")
            else:
                pass
        elif b in byte_map:
            flush_reserved()
            regs = byte_map[b]
            # Filter: keep only entries with the minimum width (drop broader overview entries)
            min_width = min(r.get('width_bytes', 1) for r in regs)
            regs = [r for r in regs if r.get('width_bytes', 1) == min_width]
            # Dedup within same width: prefer non-"Copy" names, then de-duplicate by sanitized name
            if len(regs) > 1:
                non_copy = [r for r in regs if 'Copy' not in (r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', ''))]
                if non_copy:
                    regs = non_copy
                seen = set()
                unique = []
                for r in regs:
                    n = sanitize_name(r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', ''))
                    if n not in seen:
                        seen.add(n)
                        unique.append(r)
                regs = unique
            fields = []
            for r in regs:
                name = r.get('Field Name', '') or r.get('Name', '') or r.get('Register Name', '')
                if name and name != '-':
                    clean = sanitize_name(name)
                    bits = r.get('Bits') or r.get('Bit', '')
                    desc = clean_c_string(r.get('Field Description', r.get('Register Description', r.get('Description', ''))))
                    rtype = clean_c_string(r.get('Type', ''))
                    msb, lsb = parse_bits(bits)
                    fields.append({'name': unique_name(clean, b), 'width': msb - lsb + 1, 'lsb': lsb, 'desc': desc, 'type': rtype})

            if not fields:
                # Handle multi-byte reserved synthetic entries
                wb = regs[0].get('width_bytes', 1)
                if wb > 1 and regs[0].get('_synthetic'):
                    lines.append(f"    uint8_t reserved_{b}[{wb}];  /**< r{b}.. */")
                    for k in range(1, wb):
                        consumed_bytes.add(b + k)
                else:
                    if reserved_len == 0:
                        reserved_start = b
                    reserved_len += 1
            elif len(fields) == 1 and fields[0]['width'] == 8:
                f = fields[0]
                width_bytes = regs[0].get('width_bytes', 1)
                lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=4))
                if width_bytes == 2:
                    lines.append(f"    uint16_t {f['name']};  /* r{b} */")
                elif width_bytes == 4:
                    lines.append(f"    uint32_t {f['name']};  /* r{b} */")
                elif width_bytes > 1:
                    lines.append(f"    uint8_t {f['name']}[{width_bytes}];  /* r{b} */")
                else:
                    lines.append(f"    uint8_t {f['name']};  /* r{b} */")
            else:
                fields.sort(key=lambda x: x['lsb'])
                struct_lines = []
                current_bit = 0
                for f in fields:
                    gap = f['lsb'] - current_bit
                    if gap > 0:
                        struct_lines.append(f"            uint8_t _pad_{b}_{current_bit} : {gap};")
                    lines.extend(format_doxygen(f['name'], f['desc'], f['type'], indent_level=8))
                    msb_bit = f['lsb'] + f['width'] - 1
                    if f['width'] == 1:
                        bit_note = f"r{b}.{f['lsb']}"
                    else:
                        bit_note = f"r{b}.{msb_bit}-{f['lsb']}"
                    struct_lines.append(f"            uint8_t {f['name']} : {f['width']};  /* {bit_note} */")
                    current_bit = f['lsb'] + f['width']
                if current_bit < 8:
                    struct_lines.append(f"            uint8_t _pad_{b}_{current_bit} : {8 - current_bit};")

                lines.append(f"    union {{")
                lines.append(f"        struct {{")
                for line in struct_lines:
                    lines.append(line)
                lines.append(f"        }};")
                lines.append(f"        uint8_t {_derive_accessor(fields, b)};")
                lines.append(f"    }};")
        else:
            if reserved_len == 0:
                reserved_start = b
            reserved_len += 1

    flush_reserved()

    lines.append("")
    lines.append(f"}} {struct_name};")
    lines.append("")
    lines.append("#if __STDC_VERSION__ >= 202311L")
    lines.append(f"static_assert(sizeof({struct_name}) == {num_bytes}, \"{struct_name} must be exactly {num_bytes} bytes\");")
    lines.append("#elif __STDC_VERSION__ >= 201112L")
    lines.append(f"_Static_assert(sizeof({struct_name}) == {num_bytes}, \"{struct_name} must be exactly {num_bytes} bytes\");")
    lines.append("#elif defined(__GNUC__) || defined(__clang__)")
    lines.append(f"_Static_assert(sizeof({struct_name}) == {num_bytes}, \"{struct_name} must be exactly {num_bytes} bytes\");")
    lines.append("#else")
    lines.append(f"/* verify manually: sizeof({struct_name}) must be {num_bytes} */")
    lines.append("#endif")
    lines.append("")
    lines.append(f"#endif  // {guard_name}")

    return lines


def _write_header(lines, output_path):
    """Write lines to a header file."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path
    except Exception as e:
        print(f"Error writing file: {e}")
        return None


def generate_c_header(registers, output_path):
    """Generate the low-memory (bytes 0-127) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=0, byte_end=128,
        struct_name="cmis_low_memory_t",
        header_comment="CMIS Low Memory Register Map",
        guard_name="__CMIS_LOW_MEMORY_H"
    )
    return _write_header(lines, output_path)


def generate_page00h_header(registers, output_path):
    """Generate the Page 00h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_00h_t",
        header_comment="CMIS Page 00h Register Map",
        guard_name="__CMIS_PAGE_00H_H"
    )
    return _write_header(lines, output_path)


def generate_page01h_header(registers, output_path):
    """Generate the Page 01h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_01h_t",
        header_comment="CMIS Page 01h Register Map",
        guard_name="__CMIS_PAGE_01H_H"
    )
    return _write_header(lines, output_path)


def generate_page02h_header(registers, output_path):
    """Generate the Page 02h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_02h_t",
        header_comment="CMIS Page 02h Register Map",
        guard_name="__CMIS_PAGE_02H_H"
    )
    return _write_header(lines, output_path)


def generate_page04h_header(registers, output_path):
    """Generate the Page 04h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_04h_t",
        header_comment="CMIS Page 04h Register Map",
        guard_name="__CMIS_PAGE_04H_H"
    )
    return _write_header(lines, output_path)


def generate_page10h_header(registers, output_path):
    """Generate the Page 10h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_10h_t",
        header_comment="CMIS Page 10h Register Map",
        guard_name="__CMIS_PAGE_10H_H"
    )
    return _write_header(lines, output_path)


def generate_page11h_header(registers, output_path):
    """Generate the Page 11h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_11h_t",
        header_comment="CMIS Page 11h Register Map",
        guard_name="__CMIS_PAGE_11H_H"
    )
    return _write_header(lines, output_path)


def generate_page12h_header(registers, output_path):
    """Generate the Page 12h upper-memory (bytes 128-255) C header."""
    lines = _build_struct_lines(
        registers,
        byte_start=128, byte_end=256,
        struct_name="cmis_page_12h_t",
        header_comment="CMIS Page 12h Register Map",
        guard_name="__CMIS_PAGE_12H_H"
    )
    return _write_header(lines, output_path)


def generate_page13h_header(registers, output_path):
    """Generate the Page 13h upper-memory (bytes 128-255) C header."""
    return _write_header(_build_struct_lines(registers, 128, 256, "cmis_page_13h_t", "CMIS Page 13h Register Map", "__CMIS_PAGE_13H_H"), output_path)


def generate_page14h_header(registers, output_path):
    """Generate the Page 14h upper-memory (bytes 128-255) C header."""
    return _write_header(_build_struct_lines(registers, 128, 256, "cmis_page_14h_t", "CMIS Page 14h Register Map", "__CMIS_PAGE_14H_H"), output_path)


def generate_page2fh_header(registers, output_path):
    """Generate the Page 2Fh upper-memory (bytes 128-255) C header."""
    return _write_header(_build_struct_lines(registers, 128, 256, "cmis_page_2fh_t", "CMIS Page 2Fh Register Map", "__CMIS_PAGE_2FH_H"), output_path)


def generate_page9fh_header(registers, output_path):
    """Generate the Page 9Fh upper-memory (bytes 128-255) C header with CDB command structs."""
    lines = _build_struct_lines(registers, 128, 256, "cmis_page_9fh_t", "CMIS Page 9Fh Register Map", "__CMIS_PAGE_9FH_H")
    cdb_structs = '''
/* ================================================================
 * CDB Command 0000h: Query Status (section 9.3.1)
 *
 * This command retrieves password acceptance status and module
 * operational state. The host writes the command struct, and the
 * module responds by overwriting r134-r137 with the reply.
 *
 * Command (host -> module):
 *   CMDID       = 0x0000
 *   EPLLength   = 0 (EPL not used)
 *   LPLLength   = 2
 *   CdbChkCode  = 1's complement sum of r128-132 + LPL bytes
 *   ResponseDelay = delay in ms before module responds (U16)
 *
 * Reply (module -> host):
 *   RPLLength   = 2 (encoded, see Table 8-178)
 *   RPLChkCode  = 1's complement sum of r134-137 + LPL bytes
 *   Length      = 2 (payload length including this byte)
 *   Status      = 0x00: Module Boot Up
 *                 0x01: Host Password Accepted
 *                 0x80-0xFF: Module-specific custom status
 * ================================================================ */

/** @brief CDB 0000h Query Status - Command (host -> module) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0000 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (computed by host) */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    uint16_t ResponseDelay;     /* r136-137: U16 response delay in ms (0 = immediate) */
    uint8_t  _pad[118];         /* r138-255: unused */
} cdb_0000h_cmd_t;

/** @brief CDB 0000h Query Status - Reply (module -> host) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0000 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (computed by host, unchanged by module) */
    uint8_t  RPLLength;         /* r134 = 2 (encoded per Table 8-178) */
    uint8_t  RPLChkCode;        /* r135 (computed by module) */
    uint8_t  Length;            /* r136 = 2 (payload length including this byte) */
    uint8_t  Status;            /* r137: 0x00=BootUp, 0x01=PasswordAccepted, 0x80+=Custom */
    uint8_t  _pad[118];         /* r138-255: unused */
} cdb_0000h_reply_t;

/* CDB Command 0001h: Enter Password (section 9.3.2)
 *
 * Submits a password for verification. On success the module
 * accepts the host password; on failure CdbStatus indicates the
 * specific reason.
 *
 * Command: LPL r136-139 carries the password (U32).
 * Reply:   No LPL data returned (RPLLength = 0).
 */

/** @brief CDB 0001h Enter Password - Command (host -> module) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0001 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133 (computed by host) */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    uint32_t Password;          /* r136-139: password to enter */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0001h_cmd_t;

/** @brief CDB 0001h Enter Password - Reply (module -> host) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0001 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 0 (no reply LPL data) */
    uint8_t  RPLChkCode;        /* r135 = 0 */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0001h_reply_t;

/* CDB Command 0002h: Change Password (section 9.3.3)
 *
 * Changes the host password. Similar to Enter Password but
 * stores a new password value instead of verifying.
 *
 * Command: LPL r136-139 carries the new password (U32).
 * Reply:   No LPL data returned (RPLLength = 0).
 */

/** @brief CDB 0002h Change Password - Command (host -> module) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0002 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133 (computed by host) */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    uint32_t NewPassword;       /* r136-139: new password to set */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0002h_cmd_t;

/** @brief CDB 0002h Change Password - Reply (module -> host) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0002 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 0 (no reply LPL data) */
    uint8_t  RPLChkCode;        /* r135 = 0 */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0002h_reply_t;

/* CDB Command 0004h: Abort Processing (section 9.3.4)
 *
 * Requests the module to abort any currently executing
 * background CDB operation. The command itself never fails.
 *
 * Command: No LPL data (LPLLength = 0, CdbChkCode = 0xFB).
 * Reply:   No LPL data returned (RPLLength = 0).
 */

/** @brief CDB 0004h Abort - Command (host -> module) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0004 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 = 0xFB (pre-computed, no LPL) */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0004h_cmd_t;

/** @brief CDB 0004h Abort - Reply (module -> host) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0004 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 0 (no reply LPL data) */
    uint8_t  RPLChkCode;        /* r135 = 0 */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0004h_reply_t;

/* CDB Commands 0040h-0045h: Features & Capabilities Inquiry (sections 9.4.1-9.4.6)
 *
 * These commands query module capabilities. The command has no
 * LPL data; the reply returns capability bitmaps in the LPL area.
 * All share the same command/reply structure, differing only in
 * CMDID, CdbChkCode, and reply payload interpretation.
 */

/** @brief CDB 0040h Module Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0040 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL data) */
    uint8_t  CdbChkCode;        /* r133 = 0xBF (pre-computed) */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];         /* r136-255 */
} cdb_0040h_cmd_t;

/** @brief CDB 0041h Firmware Management Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0041 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 = 0xBE */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0041h_cmd_t;

/** @brief CDB 0042h Performance Monitoring Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0042 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 = 0xBD */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0042h_cmd_t;

/** @brief CDB 0043h BERT and Diagnostics Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0043 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 = 0xBC */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0043h_cmd_t;

/** @brief CDB 0044h Security Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0044 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 = 0xBB (pre-computed) */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0044h_cmd_t;

/** @brief CDB 0045h VDM Features - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0045 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL data) */
    uint8_t  CdbChkCode;        /* r133 = computed (0xBA for no LPL) */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0045h_cmd_t;

/** @brief CDB 0040h Module Features - Reply */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0040 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 36 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  CDBFlags;          /* r136: CDB flags */
    uint8_t  _reserved_137;     /* r137: reserved */
    uint8_t  SupportedCMDs[32]; /* r138-169: CDB command support bitmap, 1 bit per CMD */
    uint16_t MaxCompletionTime; /* r170-171: U16 max CDB execution time (ms) */
    uint8_t  _pad[84];          /* r172-255: unused */
} cdb_0040h_reply_t;

/** @brief CDB 0041h Firmware Management Features - Reply (Table 9-9) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0041 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 18 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  _reserved_136;     /* r136: reserved (0) */
    union {
        struct {
            uint8_t AbortCmd             : 1;  /* r137.0: 0=CMD 0102h not supported, 1=supported */
            uint8_t CopyCmd              : 1;  /* r137.1: 0=CMD 0108h not supported, 1=supported */
            uint8_t SkippingErasedBlocks : 1;  /* r137.2: 0=not supported, 1=SkipErasedBlocks supported */
            uint8_t MaxDurationCoding    : 1;  /* r137.3: 0=M=1 (ms), 1=M=10 (ms) for MaxDuration fields */
            uint8_t _reserved_137_6_4    : 3;  /* r137.6-4: reserved (000b) */
            uint8_t ImageReadback        : 1;  /* r137.7: 0=not supported, 1=full image readback supported */
        };
        uint8_t r137;
    };
    uint8_t  StartCmdPayloadSize; /* r138: bytes host must extract from EPL page on Start cmd */
    uint8_t  ErasedByte;          /* r139: value representing an erased byte in FW image */
    uint8_t  ReadWriteLengthExt;  /* r140: additional read/write length in multiples of 256 bytes */
    uint8_t  WriteMechanism;      /* r141: 00h=unsupported, 01h=LPL only, 02h=LPL+EPL */
    uint8_t  ReadMechanism;       /* r142: 00h=unsupported, 01h=LPL only, 02h=LPL+EPL */
    uint8_t  HitlessRestart;      /* r143: 0=CMD Run Image causes reset, 1=hitless restart */
    uint16_t MaxDurationStart;    /* r144-145: U16 max Start cmd duration (M ms, see r137.3) */
    uint16_t MaxDurationAbort;    /* r146-147: U16 max Abort cmd duration (M ms) */
    uint16_t MaxDurationWrite;    /* r148-149: U16 max Write cmd duration (M ms) */
    uint16_t MaxDurationComplete; /* r150-151: U16 max Complete cmd duration (M ms) */
    uint16_t MaxDurationCopy;     /* r152-153: U16 max Copy cmd duration (M ms) */
    uint8_t  _pad[102];           /* r154-255: unused */
} cdb_0041h_reply_t;

/** @brief CDB 0042h Performance Monitoring Features - Reply (Table 9-10) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0042 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 32 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  SupportedPMCMDs[32];/* r136-167: PM command support bitmap (CMDs 0200h-02FFh) */
    uint8_t  _pad[88];          /* r168-255: unused */
} cdb_0042h_reply_t;

/** @brief CDB 0043h BERT and Diagnostics Features - Reply (Table 9-11) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0043 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 32 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  SupportedDiagCMDs[32];/* r136-167: diagnostics command support bitmap (CMDs 0300h-03FFh) */
    uint8_t  _pad[88];          /* r168-255: unused */
} cdb_0043h_reply_t;

/** @brief CDB 0044h Security Features - Reply (Table 9-12) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0044 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 50 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  SupportedSecCMDs[32];/* r136-167: security command support bitmap (CMDs 0400h-04FFh) */
    uint8_t  NumCertificates;   /* r168: number of public certificates available */
    uint8_t  CertChainSupported;/* r169: 0=not supported, 1=cert chain supported */
    uint8_t  CertificateFormat; /* r170: 0=not supported, 1=custom, 2=X509 */
    uint8_t  _reserved_171;     /* r171: reserved */
    uint16_t CertificateLength1;/* r172-173: leaf certificate length (bytes) */
    uint16_t CertificateLength2;/* r174-175: certificate i=1 length (0 if unsupported) */
    uint16_t CertificateLength3;/* r176-177: certificate i=2 length (0 if unsupported) */
    uint16_t CertificateLength4;/* r178-179: certificate i=3 length (0 if unsupported) */
    uint8_t  DigestLength;      /* r180: required hash digest length (bytes, 0=unsupported) */
    uint8_t  _reserved_181;     /* r181: reserved */
    uint16_t SignatureTime;     /* r182-183: max signature generation time (ms) */
    uint16_t SignatureLength;   /* r184-185: encoded/padded digest signature length (bytes) */
    uint8_t  SignatureFormat;   /* r186: 0=not supported, 1=custom, 2=X509 */
    uint8_t  SignaturePadScheme;/* r187: 0=none, 1=custom, 2=PKCS#1 v1.5, 3=PSS */
    uint8_t  _pad[68];          /* r188-255: unused */
} cdb_0044h_reply_t;

/** @brief CDB 0045h VDM Features - Reply (Table 9-13) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0045 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 1 (encoded per Table 8-179) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  SupplementSupport; /* r136: bit0=CMIS-VCS, bits7-1=reserved */
    uint8_t  _pad[119];         /* r137-255: unused */
} cdb_0045h_reply_t;

/* CDB Commands 0050h/0051h: Application Queries (sections 9.4.7-9.4.8) */

/** @brief CDB 0050h Get Application Attributes - Command (Table 9-14) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0050 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint16_t ApplicationNumber; /* r136-137: U16 app number (NAD index in bits 7-4) */
    uint8_t  _pad[118];         /* r138-255: unused */
} cdb_0050h_cmd_t;

/** @brief CDB 0050h Get Application Attributes - Reply (Table 9-14) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0050 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 20 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint16_t ApplicationNumber; /* r136-137: U16 (echo) */
    uint16_t MaxModulePower;    /* r138-139: U16 worst-case power (0.25W units) */
    int16_t  ProgOutputPowerMin;/* r140-141: S16 min programmable output power (0.01dBm) */
    int16_t  ProgOutputPowerMax;/* r142-143: S16 max programmable output power (0.01dBm) */
    uint16_t PreFECBERTx;       /* r144-145: F16 pre-FEC BER threshold */
    int16_t  RxLOSPowerThr;     /* r146-147: S16 RxLOS optical power threshold (0.1uW) */
    uint16_t RxPowerHighAlarm;  /* r148-149: U16 Rx power high alarm threshold (0.1uW) */
    uint16_t RxPowerLowAlarm;   /* r150-151: U16 Rx power low alarm threshold (0.1uW) */
    uint16_t RxPowerHighWarn;   /* r152-153: U16 Rx power high warning threshold (0.1uW) */
    uint16_t RxPowerLowWarn;    /* r154-155: U16 Rx power low warning threshold (0.1uW) */
    uint8_t  _pad[100];         /* r156-255: unused */
} cdb_0050h_reply_t;

/** @brief CDB 0051h Get Interface Code Description - Command (Table 9-15) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0051 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 3 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint16_t InterfaceID;       /* r136-137: U16 HostInterfaceID or MediaInterfaceID */
    uint8_t  InterfaceLocation; /* r138: 0=media side, 1=host side */
    uint8_t  _pad[117];         /* r139-255: unused */
} cdb_0051h_cmd_t;

/** @brief CDB 0051h Get Interface Code Description - Reply (Table 9-15) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0051 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 3 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 92 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint16_t InterfaceID;       /* r136-137: U16 (echo) */
    uint8_t  InterfaceLocation; /* r138: 0=media, 1=host (echo) */
    uint8_t  _reserved_139;     /* r139: reserved (alignment) */
    uint8_t  InterfaceName[16]; /* r140-155: ASCII short name */
    uint8_t  InterfaceDesc[48]; /* r156-203: ASCII description */
    uint16_t InterfaceDataRate; /* r204-205: F16 application bit rate (Gb/s) */
    uint16_t InterfaceLaneCount;/* r206-207: U16 parallel lane count */
    uint16_t LaneSignalingRate; /* r208-209: F16 lane signaling rate (GBd) */
    uint8_t  Modulation[16];    /* r210-225: ASCII modulation format */
    uint16_t BitsPerSymbol;     /* r226-227: U16 bits per modulation symbol */
    uint8_t  _pad[28];          /* r228-255: unused */
} cdb_0051h_reply_t;

/* CDB Firmware Management Commands (section 9.7)
 *
 * 0100h Get Firmware Info    0101h Start Download     0102h Abort Download
 * 0103h Write Block LPL      0104h Write Block EPL    0105h Read Block LPL
 * 0106h Read Block EPL       0107h Complete Download  0108h Copy Image
 * 0109h Run Image            010Ah Commit Image
 */

/** @brief CDB 0100h Get Firmware Info - Command (no LPL, CdbChkCode=0xFE) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0100 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL data) */
    uint8_t  CdbChkCode;        /* r133 = 0xFE (pre-computed) */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0100h_cmd_t;

/** @brief CDB 0100h Get Firmware Info - Reply (Table 9-17) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0100 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 110 (encoded per Table 8-178) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  FirmwareStatus;    /* r136: bit0=ImageA running, bit1=ImageB running, bit2=FactoryBoot running, bit7=image validation error */
    uint8_t  ImageInformation;  /* r137: bit0=ImageA info valid, bit1=ImageB info valid, bit2=FactoryBoot info valid */
    uint8_t  ImageAMajor;       /* r138: Image A major revision */
    uint8_t  ImageAMinor;       /* r139: Image A minor revision */
    uint16_t ImageABuild;       /* r140-141: U16 Image A build number */
    uint8_t  ImageAExtraString[32];/* r142-173: Image A extra string (ASCII) */
    uint8_t  ImageBMajor;       /* r174: Image B major revision */
    uint8_t  ImageBMinor;       /* r175: Image B minor revision */
    uint16_t ImageBBuild;       /* r176-177: U16 Image B build number */
    uint8_t  ImageBExtraString[32];/* r178-209: Image B extra string (ASCII) */
    uint8_t  FactoryBootMajor;  /* r210: Factory/Boot image major revision */
    uint8_t  FactoryBootMinor;  /* r211: Factory/Boot image minor revision */
    uint16_t FactoryBootBuild;  /* r212-213: U16 Factory/Boot build number */
    uint8_t  FactoryBootExtraStr[32];/* r214-245: Factory/Boot extra string (ASCII) */
    uint8_t  _pad[10];          /* r246-255: unused */
} cdb_0100h_reply_t;

/** @brief CDB 0101h Start Firmware Download - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0101 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 (computed, LPL=4) */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;     /* r134 */
    uint8_t  _reserved_135;     /* r135 */
    uint32_t ImageSize;         /* r136-139: U32 total firmware image size (bytes) */
    uint32_t _reserved_140;     /* r140-143: reserved */
    uint8_t  VendorData[112];   /* r144-255: vendor-specific start download data */
} cdb_0101h_cmd_t;
typedef cdb_0101h_cmd_t cdb_0101h_reply_t; /* RPLLength=0, no reply data */

/** @brief CDB 0102h Abort Firmware Download - Command (no LPL, CdbChkCode=0xFC) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0102 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL) */
    uint8_t  CdbChkCode;        /* r133 = 0xFC (pre-computed) */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0102h_cmd_t;
typedef cdb_0102h_cmd_t cdb_0102h_reply_t; /* RPLLength=0, no reply data */

/** @brief CDB 0103h Write Firmware Block LPL - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0103 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132: actual FW block length in LPL */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint32_t BlockAddress;      /* r136-139: U32 starting byte address of this block */
    uint8_t  FirmwareBlock[116];/* r140-255: FW block data */
} cdb_0103h_cmd_t;
typedef cdb_0103h_cmd_t cdb_0103h_reply_t; /* RPLLength=0, no reply data */

/** @brief CDB 0104h Write Firmware Block EPL - Command (LPL has BlockAddress, EPL has block data) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0104 */
    uint16_t EPLLength;         /* r130-131: EPL block length */
    uint8_t  LPLLength;         /* r132 = 4 (BlockAddress only) */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint32_t BlockAddress;      /* r136-139: U32 starting byte address */
    uint8_t  _pad[116];         /* r140-255: unused (block data in EPL pages) */
} cdb_0104h_cmd_t;
typedef cdb_0104h_cmd_t cdb_0104h_reply_t; /* RPLLength=0, no reply data */

/** @brief CDB 0105h Read Firmware Block LPL - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0105 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 6 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint32_t BlockAddress;      /* r136-139: U32 starting byte address to read */
    uint16_t Length;            /* r140-141: U16 number of bytes to read */
    uint8_t  _pad[114];         /* r142-255: unused */
} cdb_0105h_cmd_t;

/** @brief CDB 0105h Read Firmware Block LPL - Reply */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0105 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 6 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134: encoded reply LPL length (varies) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint32_t AddressOfBlock;    /* r136-139: U32 base address of data block (echo) */
    uint8_t  ImageData[116];    /* r140-255: U8[116] firmware image data */
} cdb_0105h_reply_t;

/** @brief CDB 0106h Read Firmware Block EPL - Reply (all data in EPL, LPL unused) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0106 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 6 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134: encoded reply LPL length (0) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  _pad[120];         /* r136-255: unused (data in EPL) */
} cdb_0106h_reply_t;

/** @brief CDB 0107h Complete Firmware Download - Command (no LPL, CdbChkCode=0xF7) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0107 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL) */
    uint8_t  CdbChkCode;        /* r133 = 0xF7 (pre-computed) */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];
} cdb_0107h_cmd_t;
typedef cdb_0107h_cmd_t cdb_0107h_reply_t; /* RPLLength=0, no reply data */

/** @brief CDB 0108h Copy Firmware Image - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0108 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 1 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  CopyDirection;     /* r136: 0xAB=Image A->B, 0xBA=Image B->A */
    uint8_t  _pad[119];         /* r137-255: unused */
} cdb_0108h_cmd_t;

/** @brief CDB 0108h Copy Firmware Image - Reply */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0108 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 1 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 6 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint32_t Length;            /* r136-139: U32 number of bytes copied */
    uint8_t  CopyDirection;     /* r140: 0xAB=Image A->B, 0xBA=Image B->A (echo) */
    uint8_t  CopyStatus;        /* r141: 00h=Success, 01h=Failed */
    uint8_t  _pad[114];         /* r142-255: unused */
} cdb_0108h_reply_t;

/** @brief CDB 0109h Run Firmware Image - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0109 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _reserved_136;     /* r136: reserved (0) */
    uint8_t  ImageToRun;        /* r137: 00h=Reset, 01h=Image A, 02h=Image B, 03h=Factory */
    uint16_t DelayToReset;      /* r138-139: U16 delay in ms before reset */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0109h_cmd_t;

/** @brief CDB 0109h Run Firmware Image - Reply (no LPL data) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t RPLLength; uint8_t RPLChkCode; uint8_t _pad[120];
} cdb_0109h_reply_t;

/** @brief CDB 010Ah Commit Firmware Image - Command (no LPL) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x010A */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 (no LPL) */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_010Ah_cmd_t;

/** @brief CDB 010Ah Commit Firmware Image - Reply (no LPL data) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t RPLLength; uint8_t RPLChkCode; uint8_t _pad[120];
} cdb_010Ah_reply_t;

/* CDB Performance Monitoring Commands (section 9.8) */

/** @brief CDB 0200h Control PM - Command (Table 9-32) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0200 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;     /* r134 (undefined for command) */
    uint8_t  _reserved_135;     /* r135 (undefined for command) */
    union {
        struct {
            uint8_t LinkMode      : 1;  /* r136.0: 0b=PM Objects Independent, 1b=PM Objects share settings across all lanes */
            uint8_t _pad_136_1_7  : 7;  /* r136.1-7: reserved (0) */
        };
        uint8_t r136;
    };
    uint8_t  _reserved_137;     /* r137: reserved (0) */
    union {
        struct {
            uint8_t ClearAllStatistics : 1;  /* r138.0: 1b=clear all statistics (min, avg, max) */
            uint8_t _pad_138_1_7       : 7;  /* r138.1-7: reserved (0) */
        };
        uint8_t r138;
    };
    uint8_t  _reserved_139;     /* r139: reserved (0) */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0200h_cmd_t;

/** @brief CDB 0200h Control PM - Reply (no LPL data, RPLLength=0) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t RPLLength; uint8_t RPLChkCode; uint8_t _pad[120];
} cdb_0200h_reply_t;

/** @brief CDB 0201h Get PM Feature Information - Command (no LPL, CdbChkCode=0xFC) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t _r134; uint8_t _r135; uint8_t _pad[120];
} cdb_0201h_cmd_t;

/** @brief CDB 0201h Get PM Feature Information - Reply (Table 9-33) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0201 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 0 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 4 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  HostSideMonitors;  /* r136: bitmask, bit0=SNR, bit1=R xPower, bit2=TxBias, etc. */
    uint8_t  MediaSideMonitors; /* r137: bitmask, same encoding as HostSideMonitors */
    uint8_t  _reserved_138;     /* r138: reserved (0) */
    uint8_t  _reserved_139;     /* r139: reserved (0) */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0201h_reply_t;

/** @brief CDB 0210h Get Module PM (LPL) / 0211h (EPL) - Command (Table 9-34) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0210 or 0x0211 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 5 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    union {
        struct {
            uint8_t RecordType     : 1;  /* r136.0: 0=6-byte record, 1=8-byte record */
            uint8_t _pad_136_1_6   : 6;  /* r136.1-6: reserved */
            uint8_t ClearOnRead    : 1;  /* r136.7: 0=return data, 1=return and clear */
        };
        uint8_t r136;
    };
    uint8_t  Observables;       /* r137: bitmask of PM observables to return */
    uint8_t  _reserved_138;     /* r138: reserved */
    uint8_t  _reserved_139;     /* r139: restricted (OIF) */
    uint8_t  _reserved_140;     /* r140: custom */
    uint8_t  _pad[115];         /* r141-255 */
} cdb_0210h_cmd_t;
typedef cdb_0210h_cmd_t cdb_0211h_cmd_t;

/** @brief CDB 0210h/0211h Get Module PM - Reply (LPL: r136-255, EPL: A0h-AFh) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t RPLLength; uint8_t RPLChkCode;
    uint8_t PMData[120];        /* r136-255: PM record(s) per Table 9-34 */
} cdb_0210h_reply_t;
typedef cdb_0210h_reply_t cdb_0211h_reply_t;

/** @brief CDB 0212h Get PM Host Side (LPL) / 0213h (EPL) - Command (Table 9-35) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0212 or 0x0213 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 20 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    union {
        struct {
            uint8_t RecordType     : 1;  /* r136.0: 0=6-byte record, 1=8-byte record */
            uint8_t _pad_136_1_6   : 6;  /* r136.1-6: reserved */
            uint8_t ClearOnRead    : 1;  /* r136.7: 0=return data, 1=return and clear */
        };
        uint8_t r136;
    };
    uint8_t  _reserved_137_139[3];/* r137-139: reserved */
    uint32_t Lanes;             /* r140-143: U32 bitmask of host lanes to query */
    uint8_t  Observables;       /* r144: bitmask of PM observables */
    uint8_t  _reserved_145_147[3];/* r145-147: reserved */
    uint32_t _reserved_148_151; /* r148-151: restricted (OIF) */
    uint32_t _reserved_152_155; /* r152-155: custom */
    uint8_t  _pad[100];         /* r156-255: unused */
} cdb_0212h_cmd_t;
typedef cdb_0212h_cmd_t cdb_0213h_cmd_t;
typedef cdb_0210h_reply_t cdb_0212h_reply_t;
typedef cdb_0210h_reply_t cdb_0213h_reply_t;

/** @brief CDB 0214h Get PM Media Side (LPL) / 0215h (EPL) - Command (Table 9-36) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0214 or 0x0215 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 20 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    union {
        struct {
            uint8_t RecordType     : 1;  /* r136.0: 0=6-byte record, 1=8-byte record */
            uint8_t _pad_136_1_6   : 6;
            uint8_t ClearOnRead    : 1;  /* r136.7 */
        };
        uint8_t r136;
    };
    uint8_t  _reserved_137_139[3];
    uint32_t Lanes;             /* r140-143: U32 bitmask of media lanes to query */
    uint8_t  Observables0;      /* r144: bit0=MediaSideSNR, bit1=PAM4LTP, bit2=PreFECBER, bit3=LOL, bit4=RxLOS */
    uint8_t  Observables1;      /* r145: bit0=TxLaserBias, bit1=TxPower, bit2=RxPower, bit3=RxCDRLOL, bit4=TxCDRLOL */
    uint8_t  _reserved_146_147[2];
    uint32_t _reserved_148_151; /* r148-151: restricted (OIF) */
    uint32_t _reserved_152_155; /* r152-155: custom */
    uint8_t  _pad[100];         /* r156-255 */
} cdb_0214h_cmd_t;
typedef cdb_0214h_cmd_t cdb_0215h_cmd_t;
typedef cdb_0210h_reply_t cdb_0214h_reply_t;
typedef cdb_0210h_reply_t cdb_0215h_reply_t;

/** @brief CDB 0216h Get Data Path PM (LPL) / 0217h (EPL) - Command (Table 9-37) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0216 or 0x0217 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 20 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    union {
        struct {
            uint8_t RecordType     : 1;  /* r136.0: 0=6-byte record, 1=8-byte record */
            uint8_t _pad_136_1_6   : 6;
            uint8_t ClearOnRead    : 1;  /* r136.7 */
        };
        uint8_t r136;
    };
    uint8_t  _reserved_137_139[3];
    uint32_t DataPaths;         /* r140-143: U32 mask of Data Paths to query */
    uint8_t  Observables;       /* r144: bit0=FERC, bit1=PreFECBER, bit2=CurrPreFECBER, bit3=LOL, bit4=RxLOS */
    uint8_t  _reserved_145_147[3];
    uint32_t _reserved_148_151; /* r148-151: restricted (OIF) */
    uint32_t _reserved_152_155; /* r152-155: custom */
    uint8_t  _pad[100];         /* r156-255 */
} cdb_0216h_cmd_t;
typedef cdb_0216h_cmd_t cdb_0217h_cmd_t;
typedef cdb_0210h_reply_t cdb_0216h_reply_t;
typedef cdb_0210h_reply_t cdb_0217h_reply_t;

/** @brief CDB 0220h Get Data Path RMON Statistics - Command (Table 9-38) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0220 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    union {
        struct {
            uint8_t MonitorLocation : 1;  /* r136.0: 0=media side Rx, 1=host side Tx */
            uint8_t _pad_136_1_7    : 7;  /* r136.7-1: reserved (0) */
        };
        uint8_t r136;
    };
    uint8_t  DPID;              /* r137: U8 Data Path ID */
    uint8_t  _pad[118];         /* r138-255: unused */
} cdb_0220h_cmd_t;

/** @brief CDB 0220h Get Data Path RMON Statistics - Reply (Table 9-38, U48 = uint8_t[6] little-endian) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0220 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 120 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  FrameCount[6];             /* r136-141: U48 */
    uint8_t  OctetCount[6];             /* r142-147: U48 */
    uint8_t  BadFrameCount[6];          /* r148-153: U48 */
    uint8_t  BadOctetCount[6];          /* r154-159: U48 */
    uint8_t  MulticastCount[6];         /* r160-165: U48 */
    uint8_t  BroadcastCount[6];         /* r166-171: U48 */
    uint8_t  Packets64B[6];             /* r172-177: U48 */
    uint8_t  Packets64to127B[6];        /* r178-183: U48 */
    uint8_t  Packets128to255B[6];       /* r184-189: U48 */
    uint8_t  Packets256to511B[6];       /* r190-195: U48 */
    uint8_t  Packets512to1023B[6];      /* r196-201: U48 */
    uint8_t  Packets1024to1518B[6];     /* r202-207: U48 */
    uint8_t  PacketsLargeNonJumbo[4];   /* r208-211: U32 */
    uint8_t  PacketsJumbo[4];           /* r212-215: U32 */
    uint8_t  BadMulticastCount[4];      /* r216-219: U32 */
    uint8_t  BadBroadcastCount[4];      /* r220-223: U32 */
    uint8_t  BadPackets64B[4];          /* r224-227: U32 */
    uint8_t  BadPackets64to127B[4];     /* r228-231: U32 */
    uint8_t  BadPackets128to255B[4];    /* r232-235: U32 */
    uint8_t  BadPackets256to511B[4];    /* r236-239: U32 */
    uint8_t  BadPackets512to1023B[4];   /* r240-243: U32 */
    uint8_t  BadPackets1024to1518B[4];  /* r244-247: U32 */
    uint8_t  BadPacketsLargeNonJumbo[4];/* r248-251: U32 */
    uint8_t  BadPacketsJumbo[4];        /* r252-255: U32 */
} cdb_0220h_reply_t;

/** @brief CDB 0230h Control FEC Symbol Error Weight Histogram - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0230 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  DPID;              /* r136: Data Path ID */
    union {
        struct {
            uint8_t DecoderLocation : 1;  /* r137.0: 0=media side, 1=host side */
            uint8_t Command         : 2;  /* r137.2-1: 0=reset, 1=start, 2=stop */
            uint8_t _pad_137_3_7    : 5;
        };
        uint8_t r137;
    };
    uint8_t  _pad[118];         /* r138-255 */
} cdb_0230h_cmd_t;
typedef cdb_0230h_cmd_t cdb_0230h_reply_t; /* RPLLength=0 */

/** @brief CDB 0231h Get FEC Symbol Error Weight Histogram - Command */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0231 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  DPID;              /* r136: Data Path ID */
    union {
        struct {
            uint8_t DecoderLocation : 1;  /* r137.0: 0=media side, 1=host side */
            uint8_t _pad_137_1_7    : 7;
        };
        uint8_t r137;
    };
    uint8_t  _pad[118];         /* r138-255 */
} cdb_0231h_cmd_t;

/** @brief CDB 0231h Get FEC Symbol Error Weight Histogram - Reply (Table 9-40, U48 = uint8_t[6]) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0231 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 120 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  DPID;              /* r136: U8 (echo) */
    uint8_t  DecoderLocation;   /* r137: U8 (echo) */
    uint8_t  CorrectionCapability;/* r138: U8 max correctable symbols per codeword */
    uint8_t  _reserved_139;     /* r139: reserved (pad for alignment) */
    uint8_t  UncorrectableCount[6];     /* r140-145: U48 */
    uint8_t  ErrorWeight0Count[6];      /* r146-151: U48 error-free frames */
    uint8_t  ErrorWeight1Count[6];      /* r152-157: U48 1 corrected symbol */
    uint8_t  ErrorWeight2Count[6];      /* r158-163: U48 2 corrected symbols */
    uint8_t  ErrorWeight3Count[6];      /* r164-169: U48 3 corrected symbols */
    uint8_t  ErrorWeight4Count[6];      /* r170-175: U48 4 corrected symbols */
    uint8_t  ErrorWeight5Count[6];      /* r176-181: U48 5 corrected symbols */
    uint8_t  ErrorWeight6Count[6];      /* r182-187: U48 6 corrected symbols */
    uint8_t  ErrorWeight7Count[6];      /* r188-193: U48 7 corrected symbols */
    uint8_t  ErrorWeight8Count[6];      /* r194-199: U48 8 corrected symbols */
    uint8_t  ErrorWeight9Count[6];      /* r200-205: U48 9 corrected symbols */
    uint8_t  ErrorWeight10Count[6];     /* r206-211: U48 10 corrected symbols */
    uint8_t  ErrorWeight11Count[6];     /* r212-217: U48 11 corrected symbols */
    uint8_t  ErrorWeight12Count[6];     /* r218-223: U48 12 corrected symbols */
    uint8_t  ErrorWeight13Count[6];     /* r224-229: U48 13 corrected symbols */
    uint8_t  ErrorWeight14Count[6];     /* r230-235: U48 14 corrected symbols */
    uint8_t  ErrorWeight15Count[6];     /* r236-241: U48 15 corrected symbols */
    uint8_t  ErrorWeight16Count[6];     /* r242-247: U48 16 corrected symbols */
    uint8_t  HighErrorWeightCount[6];   /* r248-253: U48 17+ corrected symbols */
    uint16_t BER;               /* r254-255: F16 pre-FEC BER */
} cdb_0231h_reply_t;

/** @brief CDB 0232h Control Max FEC Symbol Error Weight - Command (Table 9-41) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0232 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  DPID;              /* r136: Data Path ID */
    union {
        struct {
            uint8_t DecoderLocation : 1;  /* r137.0: 0=media side, 1=host side */
            uint8_t Command         : 2;  /* r137.2-1: 0=reset, 1=start, 2=stop */
            uint8_t _pad_137_3_7    : 5;  /* r137.7-3: reserved (0) */
        };
        uint8_t r137;
    };
    uint8_t  _pad[118];         /* r138-255 */
} cdb_0232h_cmd_t;

/** @brief CDB 0232h Control Max FEC Symbol Error Weight - Reply (no LPL data) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID; uint16_t EPLLength; uint8_t LPLLength; uint8_t CdbChkCode;
    uint8_t RPLLength; uint8_t RPLChkCode; uint8_t _pad[120];
} cdb_0232h_reply_t;

/** @brief CDB 0233h Get Max FEC Symbol Error Weight - Command (Table 9-42) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0233 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _reserved_136;     /* r136: reserved (0) */
    union {
        struct {
            uint8_t DecoderLocation : 1;  /* r137.0: 0=media side, 1=host side */
            uint8_t _pad_137_1_7    : 7;  /* r137.7-1: reserved (0) */
        };
        uint8_t r137;
    };
    uint8_t  _pad[118];         /* r138-255 */
} cdb_0233h_cmd_t;

/** @brief CDB 0233h Get Max FEC Symbol Error Weight - Reply (Table 9-42) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0233 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 2 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 20 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  _reserved_136;     /* r136: echoed (0) */
    uint8_t  DecoderLocation;   /* r137: echoed */
    uint16_t CorrectionCapability;/* r138-139: U16 max correctable symbols per codeword */
    uint16_t MaxErrorWeightDPID1;/* r140-141: U16 max error weight DPID 1 */
    uint16_t MaxErrorWeightDPID2;/* r142-143: DPID 2 */
    uint16_t MaxErrorWeightDPID3;/* r144-145: DPID 3 */
    uint16_t MaxErrorWeightDPID4;/* r146-147: DPID 4 */
    uint16_t MaxErrorWeightDPID5;/* r148-149: DPID 5 */
    uint16_t MaxErrorWeightDPID6;/* r150-151: DPID 6 */
    uint16_t MaxErrorWeightDPID7;/* r152-153: DPID 7 */
    uint16_t MaxErrorWeightDPID8;/* r154-155: DPID 8 */
    uint8_t  _pad[100];         /* r156-255 */
} cdb_0233h_reply_t;

/* CDB Data Monitoring Commands (section 9.9) */

/** @brief CDB 0280h Data Monitoring Controls - Command (Table 9-44) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0280 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  _reserved_136;     /* r136: reserved (0) */
    uint8_t  _reserved_137;     /* r137: reserved (0) */
    union {
        struct {
            uint8_t ClearAllStatistics : 1;  /* r138.0: 0=no op, 1=clear all statistics */
            uint8_t _pad_138_1_7       : 7;  /* r138.1-7: reserved (0) */
        };
        uint8_t r138;
    };
    uint8_t  _reserved_139;     /* r139: reserved (0) */
    uint8_t  _pad[116];         /* r140-255: unused */
} cdb_0280h_cmd_t;

/** @brief CDB 0280h Data Monitoring Controls - Reply (no LPL data, RPLLength=0) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0280 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 4 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 0 (no reply LPL) */
    uint8_t  RPLChkCode;        /* r135 = 0 */
    uint8_t  _pad[120];         /* r136-255: unused */
} cdb_0280h_reply_t;

/** @brief CDB 0290h Temperature Histogram - Command (Table 9-46) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0290 */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 1 */
    uint8_t  CdbChkCode;        /* r133: computed by host */
    uint8_t  _reserved_134;
    uint8_t  _reserved_135;
    uint8_t  SubCommands;       /* r136: bit1=save to NVR, bit2=clear, bit3=read from NVR */
    uint8_t  _pad[119];         /* r137-255: unused */
} cdb_0290h_cmd_t;

/** @brief CDB 0290h Temperature Histogram - Reply (Table 9-46, all U32 little-endian) */
typedef struct __attribute__((packed)) {
    uint16_t CMDID;             /* r128-129 = 0x0290 (echo) */
    uint16_t EPLLength;         /* r130-131 = 0 */
    uint8_t  LPLLength;         /* r132 = 1 */
    uint8_t  CdbChkCode;        /* r133 (unchanged) */
    uint8_t  RPLLength;         /* r134 = 52 (encoded) */
    uint8_t  RPLChkCode;        /* r135: computed by module */
    uint8_t  SubCommands;       /* r136: echo of host-written value */
    uint8_t  NHoursToNextWrite; /* r137: 0=no indication, 1-255=hours until next NVR write */
    uint16_t _reserved_138;     /* r138-139: reserved (0) */
    uint32_t TotalSeconds;      /* r140-143: U32 total accumulation time (seconds) */
    uint32_t TenDegBinBelowM5;  /* r144-147: U32 seconds below -5 degC */
    uint32_t TenDegBin0;        /* r148-151: U32 seconds in [-5, 5[ degC */
    uint32_t TenDegBin10;       /* r152-155: U32 seconds in [5, 15[ degC */
    uint32_t TenDegBin20;       /* r156-159: U32 seconds in [15, 25[ degC */
    uint32_t TenDegBin30;       /* r160-163: U32 seconds in [25, 35[ degC */
    uint32_t TenDegBin40;       /* r164-167: U32 seconds in [35, 45[ degC */
    uint32_t TenDegBin50;       /* r168-171: U32 seconds in [45, 55[ degC */
    uint32_t TenDegBin60;       /* r172-175: U32 seconds in [55, 65[ degC */
    uint32_t TenDegBin70;       /* r176-179: U32 seconds in [65, 75[ degC */
    uint32_t TenDegBin80;       /* r180-183: U32 seconds in [75, 85[ degC */
    uint32_t TenDegBinAbove85;  /* r184-187: U32 seconds above 85 degC */
    uint8_t  _pad[68];          /* r188-255: unused */
} cdb_0290h_reply_t;

#if __STDC_VERSION__ >= 202311L
static_assert(sizeof(cdb_0000h_cmd_t) == 128, "cdb_0000h_cmd_t != 128");
static_assert(sizeof(cdb_0000h_reply_t) == 128, "cdb_0000h_reply_t != 128");
static_assert(sizeof(cdb_0001h_cmd_t) == 128, "cdb_0001h_cmd_t != 128");
static_assert(sizeof(cdb_0001h_reply_t) == 128, "cdb_0001h_reply_t != 128");
static_assert(sizeof(cdb_0002h_cmd_t) == 128, "cdb_0002h_cmd_t != 128");
static_assert(sizeof(cdb_0002h_reply_t) == 128, "cdb_0002h_reply_t != 128");
static_assert(sizeof(cdb_0004h_cmd_t) == 128, "cdb_0004h_cmd_t != 128");
static_assert(sizeof(cdb_0004h_reply_t) == 128, "cdb_0004h_reply_t != 128");
static_assert(sizeof(cdb_0040h_cmd_t) == 128, "cdb_0040h_cmd_t != 128");
static_assert(sizeof(cdb_0041h_cmd_t) == 128, "cdb_0041h_cmd_t != 128");
static_assert(sizeof(cdb_0042h_cmd_t) == 128, "cdb_0042h_cmd_t != 128");
static_assert(sizeof(cdb_0043h_cmd_t) == 128, "cdb_0043h_cmd_t != 128");
static_assert(sizeof(cdb_0044h_cmd_t) == 128, "cdb_0044h_cmd_t != 128");
static_assert(sizeof(cdb_0045h_cmd_t) == 128, "cdb_0045h_cmd_t != 128");
static_assert(sizeof(cdb_0040h_reply_t) == 128, "cdb_0040h_reply_t != 128");
static_assert(sizeof(cdb_0041h_reply_t) == 128, "cdb_0041h_reply_t != 128");
static_assert(sizeof(cdb_0042h_reply_t) == 128, "cdb_0042h_reply_t != 128");
static_assert(sizeof(cdb_0043h_reply_t) == 128, "cdb_0043h_reply_t != 128");
static_assert(sizeof(cdb_0044h_reply_t) == 128, "cdb_0044h_reply_t != 128");
static_assert(sizeof(cdb_0045h_reply_t) == 128, "cdb_0045h_reply_t != 128");
static_assert(sizeof(cdb_0040h_reply_t) == 128, "cdb_0040h_reply_t != 128");
static_assert(sizeof(cdb_0041h_reply_t) == 128, "cdb_0041h_reply_t != 128");
static_assert(sizeof(cdb_0042h_reply_t) == 128, "cdb_0042h_reply_t != 128");
static_assert(sizeof(cdb_0043h_reply_t) == 128, "cdb_0043h_reply_t != 128");
static_assert(sizeof(cdb_0044h_reply_t) == 128, "cdb_0044h_reply_t != 128");
static_assert(sizeof(cdb_0045h_reply_t) == 128, "cdb_0045h_reply_t != 128");
static_assert(sizeof(cdb_0050h_cmd_t) == 128, "cdb_0050h_cmd_t != 128");
static_assert(sizeof(cdb_0050h_reply_t) == 128, "cdb_0050h_reply_t != 128");
static_assert(sizeof(cdb_0051h_cmd_t) == 128, "cdb_0051h_cmd_t != 128");
static_assert(sizeof(cdb_0051h_reply_t) == 128, "cdb_0051h_reply_t != 128");
#elif __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(cdb_0000h_cmd_t) == 128, "cdb_0000h_cmd_t != 128");
_Static_assert(sizeof(cdb_0000h_reply_t) == 128, "cdb_0000h_reply_t != 128");
_Static_assert(sizeof(cdb_0001h_cmd_t) == 128, "cdb_0001h_cmd_t != 128");
_Static_assert(sizeof(cdb_0001h_reply_t) == 128, "cdb_0001h_reply_t != 128");
_Static_assert(sizeof(cdb_0002h_cmd_t) == 128, "cdb_0002h_cmd_t != 128");
_Static_assert(sizeof(cdb_0002h_reply_t) == 128, "cdb_0002h_reply_t != 128");
_Static_assert(sizeof(cdb_0004h_cmd_t) == 128, "cdb_0004h_cmd_t != 128");
_Static_assert(sizeof(cdb_0004h_reply_t) == 128, "cdb_0004h_reply_t != 128");
_Static_assert(sizeof(cdb_0040h_cmd_t) == 128, "cdb_0040h_cmd_t != 128");
_Static_assert(sizeof(cdb_0040h_reply_t) == 128, "cdb_0040h_reply_t != 128");
_Static_assert(sizeof(cdb_0041h_cmd_t) == 128, "cdb_0041h_cmd_t != 128");
_Static_assert(sizeof(cdb_0041h_reply_t) == 128, "cdb_0041h_reply_t != 128");
_Static_assert(sizeof(cdb_0042h_cmd_t) == 128, "cdb_0042h_cmd_t != 128");
_Static_assert(sizeof(cdb_0042h_reply_t) == 128, "cdb_0042h_reply_t != 128");
_Static_assert(sizeof(cdb_0043h_cmd_t) == 128, "cdb_0043h_cmd_t != 128");
_Static_assert(sizeof(cdb_0043h_reply_t) == 128, "cdb_0043h_reply_t != 128");
_Static_assert(sizeof(cdb_0044h_cmd_t) == 128, "cdb_0044h_cmd_t != 128");
_Static_assert(sizeof(cdb_0044h_reply_t) == 128, "cdb_0044h_reply_t != 128");
_Static_assert(sizeof(cdb_0045h_cmd_t) == 128, "cdb_0045h_cmd_t != 128");
_Static_assert(sizeof(cdb_0045h_reply_t) == 128, "cdb_0045h_reply_t != 128");
_Static_assert(sizeof(cdb_0050h_cmd_t) == 128, "cdb_0050h_cmd_t != 128");
_Static_assert(sizeof(cdb_0050h_reply_t) == 128, "cdb_0050h_reply_t != 128");
_Static_assert(sizeof(cdb_0051h_cmd_t) == 128, "cdb_0051h_cmd_t != 128");
_Static_assert(sizeof(cdb_0051h_reply_t) == 128, "cdb_0051h_reply_t != 128");
#elif defined(__GNUC__) || defined(__clang__)
_Static_assert(sizeof(cdb_0000h_cmd_t) == 128, "cdb_0000h_cmd_t != 128");
_Static_assert(sizeof(cdb_0000h_reply_t) == 128, "cdb_0000h_reply_t != 128");
_Static_assert(sizeof(cdb_0001h_cmd_t) == 128, "cdb_0001h_cmd_t != 128");
_Static_assert(sizeof(cdb_0001h_reply_t) == 128, "cdb_0001h_reply_t != 128");
_Static_assert(sizeof(cdb_0002h_cmd_t) == 128, "cdb_0002h_cmd_t != 128");
_Static_assert(sizeof(cdb_0002h_reply_t) == 128, "cdb_0002h_reply_t != 128");
_Static_assert(sizeof(cdb_0004h_cmd_t) == 128, "cdb_0004h_cmd_t != 128");
_Static_assert(sizeof(cdb_0004h_reply_t) == 128, "cdb_0004h_reply_t != 128");
_Static_assert(sizeof(cdb_0040h_cmd_t) == 128, "cdb_0040h_cmd_t != 128");
_Static_assert(sizeof(cdb_0040h_reply_t) == 128, "cdb_0040h_reply_t != 128");
_Static_assert(sizeof(cdb_0041h_cmd_t) == 128, "cdb_0041h_cmd_t != 128");
_Static_assert(sizeof(cdb_0041h_reply_t) == 128, "cdb_0041h_reply_t != 128");
_Static_assert(sizeof(cdb_0042h_cmd_t) == 128, "cdb_0042h_cmd_t != 128");
_Static_assert(sizeof(cdb_0042h_reply_t) == 128, "cdb_0042h_reply_t != 128");
_Static_assert(sizeof(cdb_0043h_cmd_t) == 128, "cdb_0043h_cmd_t != 128");
_Static_assert(sizeof(cdb_0043h_reply_t) == 128, "cdb_0043h_reply_t != 128");
_Static_assert(sizeof(cdb_0044h_cmd_t) == 128, "cdb_0044h_cmd_t != 128");
_Static_assert(sizeof(cdb_0044h_reply_t) == 128, "cdb_0044h_reply_t != 128");
_Static_assert(sizeof(cdb_0045h_cmd_t) == 128, "cdb_0045h_cmd_t != 128");
_Static_assert(sizeof(cdb_0045h_reply_t) == 128, "cdb_0045h_reply_t != 128");
_Static_assert(sizeof(cdb_0050h_cmd_t) == 128, "cdb_0050h_cmd_t != 128");
_Static_assert(sizeof(cdb_0050h_reply_t) == 128, "cdb_0050h_reply_t != 128");
_Static_assert(sizeof(cdb_0051h_cmd_t) == 128, "cdb_0051h_cmd_t != 128");
_Static_assert(sizeof(cdb_0051h_reply_t) == 128, "cdb_0051h_reply_t != 128");
#else
/* verify: sizeof(all cdb_xxxx cmd/reply) must be 128 */
#endif
'''.strip().split('\n')
    # Insert before #endif
    lines = lines[:-1] + cdb_structs + [lines[-1]]
    return _write_header(['\n'.join(lines)], output_path)
