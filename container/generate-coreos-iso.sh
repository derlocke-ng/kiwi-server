#!/bin/bash
set -eo pipefail

# This script generates a Fedora CoreOS or uBlue CoreOS ISO with an embedded Ignition config.

# Script specific variables
CONFIG_FILE=""
OUTPUT_DIR_ARG=""
BUTANE_CMD="butane"
NO_ISO="false"

# --- Initial Checks ---
echo "--- generate-coreos-iso.sh Start ---"
# --- End Initial Checks ---

usage() {
    echo "Usage: $0 --config <config.yaml> --output-dir <directory> [--no-iso] [--help]"
    echo ""
    echo "Options:"
    echo "  --config <config.yaml>     Path to the YAML configuration file (required)."
    echo "  --output-dir <directory>   Directory to save the generated ISO and Ignition files (required)."
    echo "  --no-iso                   Only generate .bu and .ign files, skip ISO creation."
    echo "  --help                     Display this help message."
    exit 1
}

# --- Argument Parsing ---
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR_ARG="$2"
            shift 2
            ;;
        --no-iso)
            NO_ISO="true"
            shift
            ;;
        --help)
            usage
            ;;
        *)
            echo "Error: Unknown option to generate-coreos-iso.sh: $1" >&2
            usage
            ;;
    esac
done

# --- Argument Validation ---
if [[ -z "$CONFIG_FILE" ]]; then
    echo "Error: Config file not provided via --config." >&2
    usage
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Configuration file '$CONFIG_FILE' not found." >&2
    echo "Current directory: $(pwd)"
    echo "Listing /config/ directory:"
    ls -la /config/ # Helpful in container context
    exit 1
fi

if [[ -z "$OUTPUT_DIR_ARG" ]]; then
    echo "Error: Output directory not provided via --output-dir." >&2
    usage
fi

# --- Core ISO Generation Functions ---

# Helper: Generate Butane YAML for a single server (merged config)
generate_butane_for_server() {
    local server_key="$1"
    local config_file="$2"
    local output_butane_file="$3"
    python3 /usr/local/bin/generate-server-config.py --config "$config_file" --server "$server_key" --output "$output_butane_file"
}

# Processes a single server configuration to generate Butane, Ignition, and ISO.
process_single_server() {
    local server_key="$1"
    local config_file="$2"
    local output_dir="$3"

    echo "--- Processing server: $server_key ---"

    local server_output_dir="$output_dir/$server_key"
    mkdir -p "$server_output_dir"

    # 1. Generate Butane YAML using Python (merged config)
    local butane_file="$server_output_dir/${server_key}.bu"
    python3 /usr/local/bin/generate-server-config.py --config "$config_file" --server "$server_key" --output "$butane_file"
    echo "Generated Butane config: $butane_file"

    # 2. Continue as before: Butane -> Ignition -> ISO
    local ignition_file="$server_output_dir/${server_key}.ign"
    if ! $BUTANE_CMD --pretty --strict -o "$ignition_file" "$butane_file"; then
        echo "Error: Butane failed to convert $butane_file to $ignition_file for server $server_key." >&2
        return 1
    fi
    echo "Generated Ignition config: $ignition_file"

    if [[ "$NO_ISO" == "true" ]]; then
        echo "--no-iso flag set, skipping ISO creation for $server_key."
        echo "--- Finished processing server: $server_key ---"
        return 0
    fi

    # --- Generate ISO ---
    # Dynamically fetch the latest stable Fedora CoreOS ISO URL
    local stream_url="https://builds.coreos.fedoraproject.org/streams/stable.json"
    local latest_iso_url
    latest_iso_url=$(curl -sL "$stream_url" | python3 -c "import sys, json; print(json.load(sys.stdin)['architectures']['x86_64']['artifacts']['metal']['formats']['iso']['disk']['location'])")
    if [[ -z "$latest_iso_url" ]]; then
        echo "Error: Could not determine latest Fedora CoreOS ISO URL from $stream_url" >&2
        return 1
    fi

    # Allow override from config if present
    local base_iso_url
    base_iso_url="$latest_iso_url" # (Optional: add override logic in generate_server_config.py if needed)

    local base_iso_filename
    base_iso_filename=$(basename "$base_iso_url")
    local output_iso_cache="$OUTPUT_DIR_ARG/$base_iso_filename"

    # Use ISO from output folder if present, else download it there
    if [[ ! -f "$output_iso_cache" ]]; then
        echo "Downloading base ISO $base_iso_url to $output_iso_cache..."
        if ! curl -L -o "$output_iso_cache" "$base_iso_url"; then
            echo "Error: Failed to download base ISO for $server_key from $base_iso_url." >&2
            return 1
        fi
    else
        echo "Using cached base ISO: $output_iso_cache"
    fi

    local output_iso_path="$server_output_dir/${server_key}.iso"
    echo "Generating ISO for $server_key at $output_iso_path..."
    if ! coreos-installer iso ignition embed -f -i "$ignition_file" -o "$output_iso_path" "$output_iso_cache"; then
        echo "Error: coreos-installer failed to embed Ignition and create ISO for $server_key." >&2
        return 1
    fi
    echo "Successfully generated ISO: $output_iso_path"
    echo "--- Finished processing server: $server_key ---"
    return 0
}


# --- Main Script Logic ---
echo "--- Initial Configuration for ISO Generation ---"
echo "Config File: $CONFIG_FILE"
echo "Output Directory Root: $OUTPUT_DIR_ARG"

# Get all server keys from the config file in their defined order
mapfile -t ALL_SERVER_KEYS < <(python3 -c "import sys, yaml; [print(k) for k in yaml.safe_load(open('$CONFIG_FILE'))['servers'].keys()]")

if [[ ${#ALL_SERVER_KEYS[@]} -eq 0 ]]; then
    echo "No servers found under '.servers' in '$CONFIG_FILE'. Exiting."
    exit 0
fi

echo "Will process the following server keys in order: ${ALL_SERVER_KEYS[*]}"
mkdir -p "$OUTPUT_DIR_ARG" # Ensure base output directory exists

# Main loop to process each server
SUCCESS_COUNT=0
FAIL_COUNT=0
for server_key_to_process in "${ALL_SERVER_KEYS[@]}"; do
    if [[ -z "$server_key_to_process" ]]; then # Should not happen if parsing is correct
        echo "Warning: Encountered an empty server key. Skipping."
        continue
    fi
    if process_single_server "$server_key_to_process" "$CONFIG_FILE" "$OUTPUT_DIR_ARG"; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "Failed to process server '$server_key_to_process'."
    fi
done

echo ""
echo "--- Processing Summary ---"
echo "Successfully generated ISOs for $SUCCESS_COUNT server(s)."
if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "Failed to generate ISOs for $FAIL_COUNT server(s)."
    echo "Processing complete with errors."
    exit 1 # Exit with error if any server failed
fi

echo "All processing complete."
exit 0