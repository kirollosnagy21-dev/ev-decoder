import streamlit as st
import pandas as pd
import struct
import re
import pytesseract
from PIL import Image
import io

# --- DECODING LOGIC ---
def hex_to_float(hex_list):
    """Converts a list of 4 hex bytes into a 32-bit float."""
    try:
        hex_str = "".join(hex_list)
        return round(struct.unpack('!f', bytes.fromhex(hex_str))[0], 3)
    except:
        return 0.0

def hex_to_int(hex_list):
    """Converts a list of hex bytes into an integer."""
    try:
        hex_str = "".join(hex_list)
        return int(hex_str, 16)
    except:
        return 0

def decode_record(record_bytes):
    """Maps the 62-byte array into physical values."""
    failure_dict = {4: "HV Undervoltage", 41: "Secondary Fault", 54: "LIN Timeout"}
    
    # Extracting standard offsets based on the SRS trace structure
    fail_code = hex_to_int(record_bytes[0:2])
    target_speed = hex_to_int(record_bytes[2:4])
    act_speed = hex_to_float(record_bytes[4:8])
    
    # 12V Battery is at offset 18-21 in your previous 62-byte data
    battery_12v = hex_to_float(record_bytes[18:22]) 
    
    # Microcontroller Temp (approx offset 22)
    mcu_temp = hex_to_int(record_bytes[22:24])
    
    # MCU State usually towards the end of the block
    mcu_state = hex_to_int(record_bytes[-16:-14]) if len(record_bytes) >= 62 else 0
    
    return {
        "Failure Code (Hex)": f"0x{record_bytes[0]}{record_bytes[1]}",
        "Failure Code (Dec)": fail_code,
        "Failure Description": failure_dict.get(fail_code, f"Unknown ({fail_code})"),
        "Target Speed (RPM)": target_speed,
        "Actual Speed (RPM)": act_speed,
        "12V Battery (V)": battery_12v,
        "MCU Temp (°C)": mcu_temp,
        "MCU State": mcu_state,
        "Raw Payload": " ".join(record_length) # Includes raw bytes for reference
    }

# --- TEXT EXTRACTION ---
def extract_hex_from_text(text):
    """Finds the DID 0xFD0B and extracts the hex payload."""
    # Clean standard prefixes like 0x
    clean_text = text.replace("0x", "")
    # Find all continuous hex pairs
    hex_pairs = re.findall(r'\b[0-9A-Fa-f]{2}\b', clean_text)
    
    # Look for the start of the diagnostic payload (FD 0B)
    for i in range(len(hex_pairs) - 1):
        if hex_pairs[i].upper() == 'FD' and hex_pairs[i+1].upper() == '0B':
            return hex_pairs[i+2:] # Return everything after the identifier
    return []

# --- WEB APP UI ---
st.set_page_config(page_title="EV CAN/LIN Decoder", layout="wide")
st.title("⚡ EV Inverter Diagnostic Decoder")
st.markdown("Upload a Canoe Trace (`.txt`, `.asc`) or a **Screenshot** of the raw data payload. The app will automatically decode DID **0xFD0B** into physical engineering values.")

uploaded_file = st.file_uploader("Upload Log File or Screenshot", type=['txt', 'asc', 'png', 'jpg', 'jpeg'])

if uploaded_file is not None:
    st.info("File uploaded successfully. Processing...")
    
    raw_text = ""
    # Check if it's an image
    if uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        st.write("📸 Image detected. Running OCR text extraction...")
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Image", width=500)
        # Run Tesseract OCR
        raw_text = pytesseract.image_to_string(image)
    else:
        # It's a text/asc file
        raw_text = uploaded_file.read().decode("utf-8")
    
    # Extract the hex payload
    hex_payload = extract_hex_from_text(raw_text)
    
    if len(hex_payload) < 62:
        st.error("Could not find a valid 0xFD0B payload containing enough bytes. Please check the file/image.")
    else:
        st.success(f"Payload found! Extracted {len(hex_payload)} bytes. Decoding...")
        
        record_length = 62 # Byte length of one failure context record
        records = []
        
        # Loop through the payload and chunk it by record length
        for i in range(0, len(hex_payload), record_length):
            chunk = hex_payload[i:i+record_length]
            
            # Stop if the chunk is just FF padding (empty memory) or incomplete
            if len(chunk) == record_length and chunk[0].upper() != 'FF':
                decoded_data = decode_record(chunk)
                records.append(decoded_data)
        
        if records:
            df = pd.DataFrame(records)
            
            st.write("### 📊 Decoded Fault History")
            st.dataframe(df) # Render interactive table
            
            # Create a clean CSV export
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Data as CSV/Excel",
                data=csv,
                file_name='decoded_faults.csv',
                mime='text/csv',
            )
        else:
            st.warning("Payload was found, but it appears to be empty (filled with 'FF').")