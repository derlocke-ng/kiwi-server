#!/bin/bash
set -eo pipefail

echo "--- Entrypoint Start ---"

CONFIG_ARG=""
OUTPUT_DIR_ARG=""
EXTRA_ARGS=()

usage() {
    echo "Usage: entrypoint.sh --config <config.yaml> --output-dir <directory> [--help] [extra args]"
    exit 1
}

# Parse arguments for entrypoint.sh
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --config)
            if [[ -n "$2" && "$2" != --* ]]; then
                CONFIG_ARG="$2"
                shift 2
            else
                echo "ENTRYPOINT: Error: --config requires a value." >&2
                usage
            fi
            ;;
        --output-dir)
            if [[ -n "$2" && "$2" != --* ]]; then
                OUTPUT_DIR_ARG="$2"
                shift 2
            else
                echo "ENTRYPOINT: Error: --output-dir requires a value." >&2
                usage
            fi
            ;;
        --help)
            usage
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift 1 
            ;;
    esac
done

# Final check for vital arguments
if [[ -z "$CONFIG_ARG" || -z "$OUTPUT_DIR_ARG" ]]; then
    echo "ENTRYPOINT: ERROR - Config Arg ('$CONFIG_ARG') or Output Dir Arg ('$OUTPUT_DIR_ARG') is empty. Cannot proceed."
    exit 1
fi

# Call the main script
COMMAND_ARGS=()
COMMAND_ARGS+=(--config "$CONFIG_ARG")
COMMAND_ARGS+=(--output-dir "$OUTPUT_DIR_ARG")
COMMAND_ARGS+=("${EXTRA_ARGS[@]}")

echo "ENTRYPOINT: Executing /usr/local/bin/generate-coreos-iso.sh with args: ${COMMAND_ARGS[*]}"
echo "--- Entrypoint End ---"
exec /usr/local/bin/generate-coreos-iso.sh "${COMMAND_ARGS[@]}"
