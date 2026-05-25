import pandas as pd
import os

RAW_FILE = "arrival_log.csv"
CLEAN_FILE = "arrival_log_cleaned.csv"

print("🚀 Starting incremental cleaning...")

# --- Load new raw data, skipping malformed rows ---
# Rows where two lines were concatenated (missing newline) produce more fields
# than the header expects. on_bad_lines='skip' drops them safely and warns how many.
try:
    df_raw = pd.read_csv(RAW_FILE, on_bad_lines='warn')
except TypeError:
    # pandas < 1.3 used the old parameter name
    df_raw = pd.read_csv(RAW_FILE, error_bad_lines=False, warn_bad_lines=True)

total_loaded = len(df_raw)
print(f"📥 Loaded {total_loaded} rows from raw log")

# Count bad lines by re-reading with a line counter
bad_line_count = 0
try:
    expected_cols = len(df_raw.columns)
    with open(RAW_FILE, 'r', encoding='utf-8') as f:
        next(f)  # skip header
        for line in f:
            if line.count(',') + 1 > expected_cols:
                bad_line_count += 1
except Exception as e:
    print(f"⚠️  Could not count bad lines precisely: {e}")

if bad_line_count:
    print(f"⚠️  Skipped {bad_line_count} malformed rows (concatenated/truncated lines)")

# --- Parse timestamps safely ---
def parse_timestamp_safe(x):
    if pd.isna(x):
        return pd.NaT
    try:
        x = str(x).replace("EEST", "").replace("EET", "").strip()
        return pd.to_datetime(x, errors="coerce", utc=True)
    except Exception:
        return pd.NaT

df_raw["timestamp"] = df_raw["timestamp"].apply(parse_timestamp_safe)

# Track broken timestamps
before_timestamp = len(df_raw)
df_raw = df_raw.dropna(subset=["timestamp"])
removed_broken = before_timestamp - len(df_raw)

# --- Load existing clean data (if any) ---
if os.path.exists(CLEAN_FILE):
    df_clean_existing = pd.read_csv(CLEAN_FILE)
    if "timestamp" in df_clean_existing.columns:
        df_clean_existing["timestamp"] = pd.to_datetime(
            df_clean_existing["timestamp"], utc=True, errors="coerce"
        )
        last_clean_time = df_clean_existing["timestamp"].max()
        print(f"🕒 Last cleaned timestamp: {last_clean_time}")
        df_raw = df_raw[df_raw["timestamp"] > last_clean_time]
        print(f"🧩 Found {len(df_raw)} new rows since last clean")
    else:
        print("⚠️  Clean file has no timestamp column, cleaning everything.")
else:
    df_clean_existing = pd.DataFrame()
    print("📁 No previous clean file found — cleaning all data")

if df_raw.empty:
    print("✅ No new data to clean. Exiting.")
    exit()

# --- Cleaning steps ---

# Drop duplicates (vehicle_id + trip_id + stop_id)
before = len(df_raw)
df_raw = df_raw.drop_duplicates(subset=["vehicle_id", "trip_id", "stop_id"])
removed_dupes = before - len(df_raw)

# Remove unrealistic delays (outside ±2h)
if "delay_seconds" in df_raw.columns:
    before = len(df_raw)
    df_raw = df_raw[df_raw["delay_seconds"].between(-7200, 7200)]
    removed_unrealistic = before - len(df_raw)
else:
    removed_unrealistic = 0
    print("⚠️  No 'delay_seconds' column found, skipping delay filter.")

# --- Append cleaned new data to existing file ---
if not df_clean_existing.empty:
    df_final = pd.concat([df_clean_existing, df_raw], ignore_index=True)
else:
    df_final = df_raw

df_final.to_csv(CLEAN_FILE, index=False, encoding="utf-8")

# --- Summary ---
print("\n✅ Cleaning complete!")
print(f"🪄  Skipped {bad_line_count} malformed (concatenated) rows")
print(f"🧹  Removed {removed_broken} broken-timestamp rows")
print(f"🗑   Removed {removed_dupes} duplicates")
print(f"🚫  Removed {removed_unrealistic} unrealistic delays (>|2h|)")
print(f"📈  Appended {len(df_raw)} new cleaned rows")
print(f"💾  Total rows in clean file: {len(df_final)}")