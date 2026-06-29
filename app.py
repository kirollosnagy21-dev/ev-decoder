import streamlit as st
import pandas as pd
import struct
import re
import pytesseract
from PIL import Image

# --- SENSOR ADC TO CELSIUS LOOKUP TABLES (From SRS Document) ---
PCB_TEMP_TABLE = [
    (4050, -55), (4035, -50), (4016, -45), (3992, -40), (3959, -35), (3920, -30), 
    (3871, -25), (3813, -20), (3742, -15), (3660, -10), (3563, -5), (3453, 0), 
    (3329, 5), (3191, 10), (3041, 15), (2880, 20), (2712, 25), (2537, 30), 
    (2360, 35), (2183, 40), (2009, 45), (1840, 50), (1677, 55), (1524, 60), 
    (1380, 65), (1247, 70), (1124, 75), (1011, 80), (909, 85), (816, 90), 
    (733, 95), (658, 100), (591, 105), (531, 110), (477, 115), (429, 120), 
    (387, 125), (349, 130), (316, 135), (286, 140), (259, 145), (235, 150)
]

PM_TEMP_TABLE = [
    (4010, -40), (3980, -35), (3941, -30), (3893, -25), (3832, -20), (3759, -15), 
    (3672, -10), (3569, -5), (3451, 0), (3318, 5), (3170, 10), (3009, 15), 
    (2838, 20), (2659, 25), (2476, 30), (2291, 35), (2108, 40), (1930, 45), 
    (1760, 50), (1599, 55), (1447, 60), (1306, 65), (1178, 70), (1060, 75), 
    (952, 80), (857, 85), (770, 90), (692, 95), (622, 100), (560, 105), 
    (505, 110), (455, 115), (412, 120), (373, 125), (337, 130), (306, 135), 
    (278, 140), (254, 145), (231, 150)
]

def interpolate_temp(adc_val, lookup_table):
    """Interpolates Temperature from ADC using the SRS lookup tables."""
    if adc_val >= lookup_table[0][0]: return lookup_table[0][1]
    if adc_val <= lookup_table[-1][0]: return lookup_table[-1][1]
    
    for i in range(len(lookup_table) - 1):
        adc1, t1 = lookup_table[i]
        adc2, t2 = lookup_table[i+1]
        # ADC goes down as Temp goes up (NTC)
        if adc2 <= adc_val <= adc1:
            ratio = (adc_val - adc2) / (adc1 - adc2)
            return round(t2 + ratio * (t1 - t2), 1)
    return "Out of Range"

# --- CORE DECODING UTILS ---
def hex_to_float(hex_list):
    try:
        hex_str = "".join(hex_list)
        return round(struct.unpack('!f', bytes.fromhex(hex_str))[0], 3)
    except: return 0.0

def hex_to_int(hex_list):
    try:
        hex_str = "".join(hex_list)
        return int(hex_str, 16)
    except: return 0

def extract_hex_from_text(text):
    clean_text = re.sub(r'0[xX]', '', text)
    hex_words = re.findall(r'\b[0-9A-Fa-f]+\b', clean_text)
    continuous_hex = "".join(hex_words).upper()
    
    if 'FD0B' in continuous_hex:
        payload_str = continuous_hex.split('FD0B', 1)[1]
    else:
        payload_str = continuous_hex
        
    hex_bytes = [payload_str[i:i+2] for i in range(0, len(payload_str), 2)]
    return [b for b in hex_bytes if len(b) == 2]

# --- RECORD PARSER ---
def decode_record(rb):
    """Maps the 62-byte array to the exact specification of INV_Flex_EVan_SRS_EOL_0358#08"""
    
    # Complete Failure Dictionary from the SRS
    fail_dict = {
        0x0E: "MAX LV DC Voltage", 0x0D: "MIN LV DC Voltage", 0x0F: "MAX Flyback Voltage", 0x10: "MIN Flyback Voltage",
        0x08: "MAX HV DC Current", 0x03: "MAX HV DC Voltage", 0x04: "MIN HV DC Voltage", 0x1F: "MAX PCBA Temp (Running)",
        0x20: "MIN PCBA Temp (Run/Start)", 0x1D: "MAX Power Module Temp (Running)", 0x4A: "Motor Phase U Overcurrent",
        0x4B: "Motor Phase V Overcurrent", 0x4C: "Motor Phase W Overcurrent", 0x21: "MAX Micro Controller Temp (Running)",
        0x32: "MAX Power Module Temp (Startup)", 0x33: "MAX Temp PCBA/Micro (Startup)", 0x36: "LIN Cmd Frame Not Received",
        0x3B: "Phase U Sensor fail VCC", 0x3C: "Phase U Sensor fail GND", 0x3D: "Phase V Sensor fail VCC", 
        0x3E: "Phase V Sensor fail GND", 0x24: "HV DC Current warning", 0x5C: "Motor start up fail permanent",
        0x28: "Motor Phases MAX HW detection (HW OCP) startup", 0x52: "Motor Phase U MAX SW detect startup",
        0x54: "Motor Phase W MAX SW detect startup", 0x53: "Motor Phase V MAX SW detect startup", 0x2F: "Motor start up fail",
        0x25: "Motor Phase U MAX RMS Current", 0x26: "Motor Phase V MAX RMS Current", 0x27: "Motor Phase W MAX RMS Current",
        0x30: "Motor Phases MAX HW instantaneous Current", 0x1B: "RMS phases current balancing", 0x39: "PCB temp sensor GND",
        0x31: "Motor speed un-controlled", 0x37: "HV Discrepancy", 0x1C: "Power Module temp sensor VCC",
        0x3A: "Power Module temp sensor GND", 0x11: "PCBA temp sensor VCC", 0x2E: "Flyback voltage sensor VCC",
        0x0A: "HV battery current sensor - VCC", 0x02: "HV voltage sensor VCC", 0x0C: "LV voltage sensor VCC",
        0x41: "Flyback voltage sensor GND", 0x38: "LV voltage sensor GND", 0x3F: "Phase W Sensor VCC",
        0x40: "Phase W Sensor GND", 0x2B: "Power Module over temp warning", 0x5B: "Motor speed un-controlled permanent"
    }
    
    # State Machine Dictionary
    mcu_state_dict = {
        0: "0 (Idle Mode)", 1: "1 (Running - Open Loop)", 2: "2 (Running - Open Loop)", 
        3: "3 (Running - Closed Loop)", 4: "4 (Running - Closed Loop)", 5: "5 (Running - Closed Loop)", 
        6: "6 (Running - Closed Loop)", 10: "10 (Switch Fault)", 20: "20 (Failure Mode)", 40: "40 (Running - Open Loop)"
    }

    # Extracting standard offsets based on INV_Flex_EVan_SRS_EOL_0358#08
    fail_code = hex_to_int(rb[0:2])
    target_speed = hex_to_int(rb[2:4])
    torque_req = hex_to_float(rb[12:16])
    bus_voltage = hex_to_int(rb[16:18])
    battery_12v = hex_to_float(rb[18:22]) 
    
    pm_temp_adc = hex_to_int(rb[22:24])
    pcb_temp_adc = hex_to_int(rb[24:26])
    mcu_temp = hex_to_int(rb[26:28]) # Direct Celsius Register
    
    ia_rms = hex_to_float(rb[40:44])
    ib_rms = hex_to_float(rb[44:48])
    ic_rms = hex_to_float(rb[48:52])
    
    mcu_state_raw = hex_to_int(rb[54:56])
    
    return {
        "Failure Code": f"0x{rb[0]}{rb[1]}",
        "Failure Description": fail_dict.get(fail_code, f"Unknown ({fail_code})"),
        "MCU State": mcu_state_dict.get(mcu_state_raw, str(mcu_state_raw)),
        "Target Speed (RPM)": target_speed,
        "Torque Req (Nm)": torque_req,
        "HV DC Bus (V)": bus_voltage,
        "12V Battery (V)": battery_12v,
        "Phase A (Arms)": ia_rms,
        "Phase B (Arms)": ib_rms,
        "Phase C (Arms)": ic_rms,
        "Micro Controller Temp (°C)": mcu_temp,
        "Power Module Temp (°C)": interpolate_temp(pm_temp_adc, PM_TEMP_TABLE),
        "PCBA Temp (°C)": interpolate_temp(pcb_temp_adc, PCB_TEMP_TABLE),
        "PM Raw ADC": pm_temp_adc,
        "PCB Raw ADC": pcb_temp_adc
    }

# --- WEB APP UI ---
st.set_page_config(page_title="Flex E-Van Inverter Decoder", layout="wide")
st.title("⚡ Flex E-Van Inverter Decoder")

with st.expander("📖 Supported Data & How to Use"):
    st.markdown("""
    **This tool uses the exact memory map from `INV_Flex_EVan_SRS_EOL_0358#08`.**
    * **File Types:** Text Logs (`.txt`, `.asc`) or Image Screenshots (`.png`, `.jpg`).
    * **Input format:** Paste raw hex, CANoe logs, or screenshots. The app ignores all spaces and searches directly for the 0xFD0B identifier. 
    * **Temperatures:** ADC to Celsius conversion uses the exact NTC lookup tables defined in the SRS specifications.
    """)

st.markdown("---")

uploaded_file = st.file_uploader("Upload Trace File or Screenshot", type=['txt', 'asc', 'png', 'jpg', 'jpeg'])

if uploaded_file is not None:
    raw_text = ""
    if uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        st.write("📸 Image detected. Running OCR...")
        image = Image.open(uploaded_file)
        st.image(image, width=600)
        raw_text = pytesseract.image_to_string(image)
    else:
        raw_text = uploaded_file.read().decode("utf-8")
    
    hex_payload = extract_hex_from_text(raw_text)
    
    if len(hex_payload) < 62:
        st.error("❌ Not enough bytes found. Ensure payload contains at least 62 valid hex bytes.")
    else:
        st.success(f"✅ Extracted {len(hex_payload)} valid hex bytes. Decoding SRS variables...")
        
        record_length = 62 
        records = []
        
        for i in range(0, len(hex_payload), record_length):
            chunk = hex_payload[i:i+record_length]
            if len(chunk) == record_length and chunk[0].upper() != 'FF':
                records.append(decode_record(chunk))
        
        if records:
            df = pd.DataFrame(records)
            st.write("### 📊 Decoded Failure Context Memory")
            st.dataframe(df, use_container_width=True) 
            
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Table as CSV", data=csv, file_name='flex_evan_faults.csv', mime='text/csv')
        else:
            st.warning("⚠️ Buffer is filled with empty memory ('FF').")
