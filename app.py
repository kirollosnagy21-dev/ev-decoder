import streamlit as st
import pandas as pd
import struct
import re
import pytesseract
from PIL import Image

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

def extract_hex_from_text(text):
    """
    Robustly extracts hex bytes.
    Ignores spaces, line breaks, and '0x' prefixes.
    Locates the FD0B identifier to begin parsing.
    """
    # 1. Remove '0x' or '0X' prefixes entirely
    clean_text = re.sub(r'0[xX]', '', text)
    
    # 2. Extract valid hexadecimal 'words' (ignores standard english punctuation)
    hex_words = re.findall(r'\b[0-9A-Fa-f]+\b', clean_text)
    
    # 3. Smash them all together into one continuous uppercase string
    continuous_hex = "".join(hex_words).upper()
    
    # 4. Search for the identifier FD0B. If found, slice everything after it.
    if 'FD0B' in continuous_hex:
        payload_str = continuous_hex.split('FD0B', 1)[1]
    else:
        # If not found, assume the user pasted ONLY the raw payload
        payload_str = continuous_hex
        
    # 5. Break the string back down into pairs of 2 characters (bytes)
    hex_bytes = [payload_str[i:i+2] for i in range(0, len(payload_str), 2)]
    
    # Ensure we only return complete 2-character bytes
    return [b for b in hex_bytes if len(b) == 2]

def decode_record(record_bytes):
    """Maps the 62-byte array into physical engineering values."""
    failure_dict = {4: "HV Undervoltage", 41: "Secondary Fault", 54: "LIN Timeout"}
    
    fail_code = hex_to_int(record_bytes[0:2])
    target_speed = hex_to_int(record_bytes[2:4])
    act_speed = hex_to_float(record_bytes[4:8])
    torque_req = hex_to_float(record_bytes[12:16])
    bus_voltage = hex_to_int(record_bytes[16:18])
    battery_12v = hex_to_float(record_bytes[18:22]) 
    pm_temp = hex_to_int(record_bytes[22:24])
    pcb_temp = hex_to_int(record_bytes[24:26])
    mcu_state = hex_to_int(record_bytes[-16:-14]) if len(record_bytes) >= 62 else 0
    
    return {
        "Failure Code (Hex)": f"0x{record_bytes[0]}{record_bytes[1]}",
        "Failure Code (Dec)": fail_code,
        "Failure Description": failure_dict.get(fail_code, f"Unknown ({fail_code})"),
        "Target Speed (RPM)": target_speed,
        "Actual Speed (RPM)": act_speed,
        "Torque Req (Nm)": torque_req,
        "DC Bus (V)": bus_voltage,
        "12V Battery (V)": battery_12v,
        "PM Temp (ADC)": pm_temp,
        "PCB Temp (ADC)": pcb_temp,
        "MCU State": mcu_state
    }

# --- WEB APP UI ---
st.set_page_config(page_title="EV CAN/LIN Decoder", layout="wide")
st.title("⚡ EV Inverter Diagnostic Decoder")

# USER GUIDE SECTION
with st.expander("📖 How to format and upload your data (Click to read)"):
    st.markdown("""
    **This tool is highly flexible and accepts data in almost any format.** Here is a quick guide on getting the best results:
    
    ### Supported File Types
    * **Text Logs:** Upload Canoe `.asc` files, `.txt` files, or raw console copy-pastes.
    * **Screenshots:** Upload `.png`, `.jpg`, or `.jpeg` images of your diagnostic console. The tool uses OCR to read the text automatically.
    
    ### Formatting Rules (Or Lack Thereof)
    The parsing engine is designed to adapt to your data:
    * **Spacing doesn't matter:** `00 36 FF` works exactly the same as `0036FF`.
    * **`0x` doesn't matter:** `0x00 0x36 0xFF` works exactly the same as `00 36 FF`.
    * **Line breaks don't matter:** You can copy an entire terminal window with line breaks, words, and timestamps.
    
    ### Two Ways to Upload
    1. **Full Log with Header:** Simply upload your raw log. As long as the identifier **`FD 0B`** (or `FD0B`) is somewhere in the text, the app will automatically find it, ignore everything before it, and decode the payload.
    2. **Raw Payload Only:** If your snippet *doesn't* have `FD 0B` in it, just make sure you are pasting **only** the pure hex data (no timestamps or english words mixed in), and the app will decode it perfectly.
    """)

st.markdown("---")

# UPLOAD SECTION
uploaded_file = st.file_uploader("Upload Log File or Screenshot", type=['txt', 'asc', 'png', 'jpg', 'jpeg'])

if uploaded_file is not None:
    raw_text = ""
    
    if uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        st.write("📸 Image detected. Running OCR text extraction...")
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Image", width=500)
        raw_text = pytesseract.image_to_string(image)
    else:
        raw_text = uploaded_file.read().decode("utf-8")
    
    # Extract the hex payload using the new robust logic
    hex_payload = extract_hex_from_text(raw_text)
    
    if len(hex_payload) < 62:
        st.error("❌ Could not find a valid payload containing enough bytes. Ensure the log contains 'FD 0B' followed by the data, or just the pure hex payload.")
    else:
        st.success(f"✅ Payload found! Extracted {len(hex_payload)} valid hex bytes. Decoding...")
        
        record_length = 62 
        records = []
        
        # Loop through the payload and chunk it by record length
        for i in range(0, len(hex_payload), record_length):
            chunk = hex_payload[i:i+record_length]
            
            # Stop if the chunk is incomplete or just empty memory (FF)
            if len(chunk) == record_length and chunk[0].upper() != 'FF':
                decoded_data = decode_record(chunk)
                records.append(decoded_data)
        
        if records:
            df = pd.DataFrame(records)
            
            st.write("### 📊 Decoded Fault History")
            st.dataframe(df, use_container_width=True) 
            
            # Export CSV
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Table as CSV/Excel",
                data=csv,
                file_name='decoded_faults.csv',
                mime='text/csv',
            )
        else:
            st.warning("⚠️ Data was found, but the buffer appears to be empty (filled with 'FF').")
