#!/bin/bash
set -eo pipefail

# Default to podman if available, otherwise use docker
CONTAINER_CMD="docker"
if command -v podman &> /dev/null; then
    CONTAINER_CMD="podman"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
CONTAINER_IMAGE_NAME="coreos-iso-generator"
CONFIG_FILE=""
OUTPUT_DIR="${PROJECT_ROOT}/output"

# Function to print help
print_help() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  build                         Build the container image."
    echo "  generate <config_file>        Generate CoreOS ISOs based on the config file."
    echo "    --output-dir <dir>          Output directory for generated ISOs (default: ./output)."
    echo "    --build                     Rebuild the container image before generating ISOs."
    echo "    --no-iso                    Only generate .bu and .ign files, skip ISO creation."
    echo "  help                          Show this help message."
    echo ""
    echo "Container runtime: $CONTAINER_CMD will be used."
}

# Function to build the container
build_container() {
    echo "Building container image '$CONTAINER_IMAGE_NAME' using $CONTAINER_CMD..."
    # shellcheck disable=SC2086 # We want word splitting for DOCKER_BUILD_ARGS
    if ! "$CONTAINER_CMD" build ${DOCKER_BUILD_ARGS} -t "$CONTAINER_IMAGE_NAME" -f "${PROJECT_ROOT}/container/Dockerfile" "${PROJECT_ROOT}/container"; then
        echo "Error: Failed to build container image." >&2
        exit 1
    fi
    echo "Container image build complete."
}

# Function to generate ISOs
generate_isos() {
    # PERFORM_BUILD is a global variable set during argument parsing for the 'generate' command
    # shellcheck disable=SC2154 # PERFORM_BUILD is set in the case statement
    if [[ "${PERFORM_BUILD}" == "true" ]]; then
        echo "Rebuilding container due to --build flag..."
        build_container
    fi

    if [[ -z "$CONFIG_FILE" ]]; then
        echo "Error: Configuration file not specified for generate command." >&2
        print_help
        exit 1
    fi
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "Error: Configuration file '$CONFIG_FILE' not found." >&2
        exit 1
    fi

    mkdir -p "$OUTPUT_DIR"
    echo "Generating CoreOS ISOs..."
    echo "Config file: $(realpath "$CONFIG_FILE")"
    echo "Output directory: $(realpath "$OUTPUT_DIR")"

    # Pass --no-iso if set
    local NO_ISO_ARG=""
    if [[ "$NO_ISO" == "true" ]]; then
        NO_ISO_ARG="--no-iso"
    fi

    # Mount project root to access scripts and potentially other resources if needed by entrypoint
    # Mount config file directly to /config/config.yaml
    # Mount output directory to /output
    # Mount the scripts directory from the new location inside the container folder
    # Pass arguments to entrypoint.sh as named arguments
    # Added ',z' to the config file mount for SELinux compatibility (especially with Podman)
    if ! "$CONTAINER_CMD" run --rm \
        -v "$(realpath "$CONFIG_FILE"):/config/config.yaml:ro,z" \
        -v "$(realpath "$OUTPUT_DIR"):/output:z" \
        -v "${PROJECT_ROOT}/container:/scripts:ro,z" \
        "$CONTAINER_IMAGE_NAME" --config /config/config.yaml --output-dir /output $NO_ISO_ARG; then 
        echo "Error: Failed to run container for ISO generation." >&2
        exit 1
    fi

    echo "ISO generation process complete. Check '$OUTPUT_DIR' for results."
}

# Parse main command
COMMAND="$1"
shift

case "$COMMAND" in
    build)
        build_container
        ;;
    generate)
        if [[ -z "$1" ]]; then
            echo "Error: Missing config_file argument for generate command." >&2
            print_help
            exit 1
        fi
        CONFIG_FILE="$1"
        shift # Consume config_file

        # Initialize options
        PERFORM_BUILD="false" 
        # OUTPUT_DIR is global and has a default
        NO_ISO="false"

        while [[ "$#" -gt 0 ]]; do
            case "$1" in
                --output-dir)
                    OUTPUT_DIR="$2"
                    shift 2
                    ;;
                --build)
                    PERFORM_BUILD="true"
                    shift # Consume --build
                    ;;
                --no-iso)
                    NO_ISO="true"
                    shift # Consume --no-iso
                    ;;
                *)
                    if [[ "$1" == -* ]]; then
                        echo "Error: Unknown option '$1' for generate command." >&2
                    else
                        echo "Error: Unexpected argument '$1'. Config file should be the first argument after 'generate'." >&2
                    fi
                    print_help
                    exit 1
                    ;;
            esac
        done
        generate_isos
        ;;
    help|--help|-h)
        print_help
        ;;
    *)
        echo "Error: Unknown command '$COMMAND'." >&2
        print_help
        exit 1
        ;;
esac
