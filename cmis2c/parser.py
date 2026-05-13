"""Enhanced parser to extract registers and their Enum definitions."""

import pdfplumber
import re
import json

def clean_cell(x):
    s = str(x).strip().replace('\n', ' ').strip() if x else ""
    # Fix PDF kerning artifacts in numeric fields: "12 8" -> "128", "3- 0" -> "3-0"
    if re.match(r'^[\d\s\-]+$', s):
        s = re.sub(r'\s+', '', s)
    # Fix kerning in text: single uppercase letter + space + lowercase word -> join
    s = re.sub(r'\b([A-Z])\s+([a-z])', r'\1\2', s)
    return s

def parse_bits(bits_str):
    if not bits_str: return (7, 0)
    m = re.match(r'(\d+)-(\d+)', bits_str)
    if m: return (int(m.group(1)), int(m.group(2)))
    try: return (int(bits_str), int(bits_str))
    except: return (7, 0)

def _extract_register_tables(pdf, page_range, byte_lo, byte_hi):
    """Extract register entries from table pages within a byte range."""
    registers = []
    enums = {}
    current_byte = None

    for i in page_range:
        if i >= len(pdf.pages): break
        page = pdf.pages[i]
        tables = page.extract_tables()

        for t in tables:
            if not t: continue
            r0 = [clean_cell(x) for x in t[0]]

            is_detail = False
            has_byte_header = 'Byte' in r0 or 'Bytes' in r0
            is_bytes_table = 'Bytes' in r0 and 'Byte' not in r0
            # Detect overview/map tables (Address, Size, Subject Area, Description)
            is_overview = 'Address' in r0 or 'Subject Area' in r0
            if has_byte_header:
                if any(k in r0 for k in ['Bit', 'Bits', 'Field Name', 'Name', 'Register Name', 'Length', 'Subject Area']):
                    is_detail = True
            elif is_overview:
                is_detail = True
            elif len(r0) >= 3 and '' in r0[0]:
                c1 = r0[1]
                if re.match(r'^\d+-\d+$', c1) or re.match(r'^\d+$', c1):
                    is_detail = True

            if is_detail:
                start_row = 1
                if '' in r0[0] and not has_byte_header:
                    start_row = 0

                for row in t[start_row:]:
                    if not row: continue
                    cells = [clean_cell(x) for x in row]

                    entry = {}
                    if has_byte_header:
                        for j, cell in enumerate(cells):
                            if j < len(r0): entry[r0[j]] = cell
                        # Normalize column names
                        for key in list(entry.keys()):
                            kl = key.lower()
                            if 'description' in kl and 'Field Description' not in entry and 'Register Description' not in entry:
                                entry['Register Description'] = entry.pop(key)
                            elif 'name' in kl and 'Field Name' not in entry and 'Register Name' not in entry:
                                if 'field' in kl:
                                    entry['Field Name'] = entry.pop(key)
                                else:
                                    entry['Register Name'] = entry.pop(key)
                        # Normalize: 'Bytes' header -> 'Byte' key
                        if 'Bytes' in entry and 'Byte' not in entry:
                            entry['Byte'] = entry.pop('Bytes')
                        # Normalize: 'Subject Area' -> 'Register Name' for overview tables
                        if 'Subject Area' in entry and 'Register Name' not in entry:
                            entry['Register Name'] = entry.pop('Subject Area')
                            entry['_overview'] = True
                        # For Bytes-type tables, fix Bits: 'All' or length value -> full byte range
                        if is_bytes_table:
                            bits_val = entry.get('Bits', entry.get('Bit', ''))
                            if not bits_val or bits_val.lower() == 'all' or re.match(r'^\d+$', bits_val):
                                entry['Bits'] = '7-0'
                    elif is_overview:
                        mapping = ['Byte', '_Size', 'Register Name', 'Register Description']
                        for j, cell in enumerate(cells):
                            if j < len(mapping): entry[mapping[j]] = cell
                        entry['Bits'] = '7-0'
                        entry['_overview'] = True
                        # If subject area is blank, derive name from description
                        name_val = entry.get('Register Name', '')
                        if not name_val or name_val == '-':
                            desc = entry.get('Register Description', entry.get('Description', ''))
                            if 'custom' in desc.lower():
                                if 'non-volatile' in desc.lower():
                                    entry['Register Name'] = 'CustomInfoNV'
                                else:
                                    entry['Register Name'] = 'Custom'
                            elif 'reserved' in desc.lower():
                                entry['Register Name'] = '-'
                    else:
                        mapping = ['Byte', 'Bits', 'Field Name', 'Field Description', 'Type']
                        for j, cell in enumerate(cells):
                            if j < len(mapping): entry[mapping[j]] = cell
                    # Ensure Bits is set for overview tables
                    if is_overview and 'Bits' not in entry:
                        entry['Bits'] = '7-0'

                    if 'Byte' in entry and entry['Byte'] and entry['Byte'] not in ['-', '']:
                        current_byte = entry['Byte']
                    elif current_byte:
                        entry['Byte'] = current_byte

                    name = entry.get('Field Name') or entry.get('Name') or entry.get('Register Name', '')
                    if current_byte and name and name != '-':
                        b_str = str(entry['Byte'])
                        try:
                            if '-' in b_str:
                                val = int(b_str.split('-')[0])
                                end_val = int(b_str.split('-')[1])
                            else:
                                val = int(re.sub(r'\D', '', b_str))
                                end_val = val

                            if byte_lo <= val <= byte_hi or byte_lo <= end_val <= byte_hi:
                                registers.append(entry)
                        except ValueError:
                            pass

    # Pass 2: Extract enums from same pages
    for i in page_range:
        if i >= len(pdf.pages): break
        page = pdf.pages[i]
        text = page.extract_text() or ""
        tables = page.extract_tables()

        for t in tables:
            if not t: continue
            r0 = [clean_cell(x).lower().replace('\n', ' ') for x in t[0]]

            has_code = any(c in r0 for c in ['code', 'value', 'bit pattern', 'module state', 'encoding'])
            has_desc = 'description' in r0 or 'name' in r0 or 'state' in r0

            if has_code and has_desc:
                table_id = None
                matches = re.findall(r'Table\s(\d+-\d+)', text)
                if matches:
                    table_id = matches[-1]

                code_idx = -1
                desc_idx = -1
                name_idx = -1

                for idx, c in enumerate(r0):
                    if code_idx < 0 and ('code' in c or 'bit pattern' in c or 'module state' in c or 'encoding' in c): code_idx = idx
                    if 'value' in c and code_idx < 0: code_idx = idx
                    if 'description' in c or 'field description' in c: desc_idx = idx
                    if 'name' in c or 'state' in c and 'description' not in c: name_idx = idx

                if code_idx >= 0:
                    enum_vals = {}
                    for row in t[1:]:
                        row_vals = [clean_cell(x) for x in row]
                        code_raw = row_vals[code_idx] if code_idx < len(row_vals) else ""

                        if 'b' in code_raw.lower():
                            try:
                                code_int = int(code_raw.replace('b','').replace(' ',''), 2)
                            except: code_int = None
                        elif 'h' in code_raw.lower():
                            try:
                                code_int = int(code_raw.replace('h','').replace(' ',''), 16)
                            except: code_int = None
                        else:
                            try:
                                code_int = int(code_raw)
                            except: code_int = None

                        if code_int is not None:
                            desc = row_vals[desc_idx] if desc_idx >= 0 and desc_idx < len(row_vals) else ""
                            name = row_vals[name_idx] if name_idx >= 0 and name_idx < len(row_vals) else desc
                            if not name: name = f"val_{code_int}"
                            enum_vals[code_int] = {
                                'name': name,
                                'desc': desc
                            }

                    if enum_vals:
                        if table_id:
                            key = f"Table {table_id}"
                            if key not in enums:
                                enums[key] = {'table_id': table_id, 'values': enum_vals}
                        else:
                            enums[f"Auto_{i}_{id(t)}"] = {
                                'table_id': None,
                                'values': enum_vals
                            }

    # Link enums to registers
    for reg in registers:
        desc = reg.get('Field Description', '') or reg.get('Register Description', '') or ""
        m = re.search(r'Table\s(\d+-\d+)', desc)
        if m:
            table_ref = f"Table {m.group(1)}"
            if table_ref in enums:
                reg['enum'] = enums[table_ref]

    return registers


def extract_cmis_data(pdf_path):
    """Extract Low Memory registers (bytes 0-127) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(141, 166), 0, 127)


def extract_page00h_data(pdf_path):
    """Extract Page 00h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(160, 167), 128, 255)


def extract_page01h_data(pdf_path):
    """Extract Page 01h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(167, 184), 128, 255)


def extract_page02h_data(pdf_path):
    """Extract Page 02h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(184, 186), 128, 255)


def extract_page04h_data(pdf_path):
    """Extract Page 04h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(187, 190), 128, 255)


def extract_page10h_data(pdf_path):
    """Extract Banked Page 10h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        regs = _extract_register_tables(pdf, range(191, 207), 128, 255)
    # Inject synthetic entries from overview: 240-255 Custom
    regs.append({'Byte': '240-255', 'Bits': '7-0', 'Register Name': 'CustomInfo',
                 'Register Description': 'Custom information', '_synthetic': True})
    return regs


def extract_page11h_data(pdf_path):
    """Extract Banked Page 11h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        regs = _extract_register_tables(pdf, range(207, 219), 128, 255)
    # Inject synthetic entries for r240-255 lane mapping arrays (fill gaps)
    for lane in range(1, 9):
        bt = 240 + lane - 1
        br = 248 + lane - 1
        for b in [bt, br]:
            suffix = 'Tx' if b < 248 else 'Rx'
            regs.append({'Byte': str(b), 'Bits': '3-0',
                         'Field Name': f'MediaLaneToFiberMapping{suffix}{lane}',
                         '_synthetic': True})
            regs.append({'Byte': str(b), 'Bits': '7-4',
                         'Field Name': f'MediaLaneToWavelengthMapping{suffix}{lane}',
                         '_synthetic': True})
    return regs


def extract_page12h_data(pdf_path):
    """Extract Banked Page 12h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        regs = _extract_register_tables(pdf, range(220, 222), 128, 255)
    # Overwrite with proper synthetic entries based on overview table 8-98
    regs = [r for r in regs if not r.get('_synthetic')]
    # Filter out malformed entries with '<n>' notation
    regs = [r for r in regs if '<n>' not in str(r.get('Field Name', '')) and '<n>' not in str(r.get('Name', ''))]

    def add_array(start, count, width, base_name, desc, struct_fields=None):
        """Add synthetic entries for an 8-lane array.
        struct_fields: optional list of (name_suffix, bits, desc) for per-byte fields within a union.
        """
        for i in range(count):
            b = start + i * width
            if struct_fields:
                for sf_name, sf_bits, sf_desc in struct_fields:
                    regs.append({'Byte': str(b), 'Bits': sf_bits,
                                 'Field Name': f'{sf_name}{i+1}',
                                 'Field Description': f'{sf_desc} lane {i+1}',
                                 'width_bytes': 1, '_synthetic': True})
            elif width == 1:
                regs.append({'Byte': str(b), 'Bits': '7-0',
                             'Field Name': f'{base_name}{i+1}',
                             'Field Description': f'{desc} lane {i+1}',
                             'width_bytes': 1, '_synthetic': True})
            else:
                regs.append({'Byte': f'{b}-{b+width-1}', 'Bits': '7-0',
                             'Field Name': f'{base_name}{i+1}',
                             'Field Description': f'{desc} lane {i+1}',
                             'width_bytes': width, '_synthetic': True})

    # r128-135: GridSpacings[8] (1 byte each, 2 fields: GridSpacingTx 7-4, FineTuningEnableTx 0)
    add_array(128, 8, 1, 'GridSpacing', 'Grid spacing',
              [('GridSpacingTx', '7-4', 'Grid spacing for media lane'),
               ('FineTuningEnableTx', '0', 'Fine-tuning enabled')])
    # r136-151: ChannelOffsetNumbers[8] (S16 each)
    add_array(136, 8, 2, 'ChannelOffsetNumber', 'Channel offset number')
    # r152-167: FineTuningOffsets[8] (S16 each)
    add_array(152, 8, 2, 'FineTuningOffset', 'Fine tuning offset')
    # r168-199: LaserFrequencies[8] (U32 each)
    add_array(168, 8, 4, 'LaserFrequency', 'Laser frequency')
    # r200-215: TargetOutputPower[8] (S16 each)
    add_array(200, 8, 2, 'TargetOutputPower', 'Target output power')
    # r222-229: StatusIndicator array (1 byte each, 2 bit-fields + pad)
    add_array(222, 8, 1, 'StatusIndicator', 'Status indicator',
              [('WavelengthUnlockStatusTx', '0', 'Bool: Unlocked status indication for laser wavelength on media lane, 0b/1b: Wavelength locked/unlocked'),
               ('TuningInProgressTx', '1', '0b/1b: Tuning not in progress/in progress')])
    # r231-238: Flags[8] (1 byte each, 6 flag bits + pad)
    add_array(231, 8, 1, 'Flag', 'Flag',
              [('TuningCompleteFlagTx', '0', 'Latched Flag set after tuning has completed'),
               ('WavelengthUnlockedFlagTx', '1', 'Latched Flag indicating an unlocked wavelength condition'),
               ('InvalidChannelNumberFlagTx', '2', 'Latched Flag indicating an invalid channel number was selected'),
               ('TuningNotAcceptedFlagTx', '3', 'Latched Flag indicating a failed tuning operation: module temporarily unable to serve a tuning request'),
               ('FineTuningOutOfRangeFlagTx', '4', 'Latched Flag indicating a fine-tuning value outside the allowed range was given'),
               ('TargetOutputPowerOORFlagTx', '5', 'Latched Flag indicating a target output power value outside the allowed range was entered')])
    # r239-246: Masks[8] (1 byte each, 6 mask bits + pad)
    add_array(239, 8, 1, 'Mask', 'Mask',
              [('TuningCompleteMaskTx', '0', 'Mask for TuningCompleteFlagTx'),
               ('WavelengthUnlockedMaskTx', '1', 'Mask for WavelengthUnlockedFlagTx'),
               ('InvalidChannelMaskTx', '2', 'Mask for InvalidChannelNumberFlagTx'),
               ('TuningNotAcceptedMaskTx', '3', 'Mask for TuningNotAcceptedFlagTx'),
               ('FineTuningOutOfRangeMaskTx', '4', 'Mask for FineTuningOutOfRangeFlagTx'),
               ('TargetOutputPowerOORMaskTx', '5', 'Mask for TargetOutputPowerOORFlagTx')])
    # r216-221: Reserved[6]
    regs.append({'Byte': '216-221', 'Bits': '7-0', 'Register Name': '-',
                 'Register Description': 'Reserved[6]', '_synthetic': True})
    # r247-255: Reserved[9]
    regs.append({'Byte': '247-255', 'Bits': '7-0', 'Register Name': '-',
                 'Register Description': 'Reserved[9]', '_synthetic': True})
    return regs


def extract_page13h_data(pdf_path):
    """Extract Banked Page 13h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(223, 243), 128, 255)


def extract_page14h_data(pdf_path):
    """Extract Banked Page 14h Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        regs = _extract_register_tables(pdf, range(243, 249), 128, 255)
    # Filter out malformed entries from global-bit-position tables (Bits > 7 in multi-byte registers)
    regs = [r for r in regs if parse_bits(r.get('Bits', '') or r.get('Bit', ''))[0] <= 7]
    # r140-149: Reserved[10] (overwrite any parsed entries for this range)
    regs = [r for r in regs if not (140 <= int(str(r.get('Byte','0')).split('-')[0]) <= 149 and not r.get('_synthetic'))]
    regs.append({'Byte': '140-149', 'Bits': '7-0', 'Register Name': '-',
                 'Register Description': 'Reserved[10]', '_synthetic': True})
    # r130-131: Custom[2] (arrayize as 2 bytes)
    for b in range(130, 132):
        regs.append({'Byte': str(b), 'Bits': '7-0', 'Field Name': f'Custom{b-129}',
                     'Field Description': f'Custom byte {b-129}', 'width_bytes': 1, '_synthetic': True})
    return regs


def extract_page2fh_data(pdf_path):
    """Extract Banked Page 2Fh Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return _extract_register_tables(pdf, range(278, 281), 128, 255)


def extract_page9fh_data(pdf_path):
    """Extract Banked Page 9Fh Upper Memory registers (bytes 128-255) from CMIS PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        regs = _extract_register_tables(pdf, range(282, 290), 128, 255)
    # Keep only header registers (r128-135), add LPL array
    regs = [r for r in regs if int(str(r.get('Byte','128')).split('-')[0]) < 136]
    # Remove enum from RPLLength (it's a raw value, not an enumeration)
    for r in regs:
        if 'RPLLength' in str(r.get('Field Name', '')):
            r.pop('enum', None)
    # Add LPL array (r136-255 = 120 bytes)
    regs.append({'Byte': '136-255', 'Bits': '7-0', 'Field Name': 'LPL',
                 'Field Description': 'Local Payload (120 bytes)', 'width_bytes': 120, '_synthetic': True})
    return regs
