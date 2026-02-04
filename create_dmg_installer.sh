#!/usr/bin/env bash
############################################################################
# PowerTrader AI - macOS DMG Installer Creation Script
#
# This script creates a professional DMG installer for the PowerTrader AI
# macOS application bundle.
#
# Prerequisites:
#   - create-dmg installed (brew install create-dmg)
#   - PowerTrader_AI.app already built in dist/
#   - Custom icon available at assets/icon.icns
#
# Output:
#   dist/PowerTrader_AI.dmg - Ready-to-distribute installer
#
# Usage:
#   ./create_dmg_installer.sh [options]
#
# Options:
#   --clean    Remove existing DMG before creating new one
#   --help     Show this help message
#
# Author: PowerTrader AI Contributors
# License: Apache 2.0
############################################################################

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
APP_BUNDLE="${DIST_DIR}/PowerTrader_AI.app"
ICON_FILE="${PROJECT_ROOT}/assets/icon.icns"
OUTPUT_DMG="${DIST_DIR}/PowerTrader_AI.dmg"

# DMG settings
VOLNAME="PowerTrader AI"
WINDOW_SIZE="600 400"
ICON_SIZE=100
APP_ICON_POS="175 185"
APPLICATIONS_LINK_POS="425 185"
FILESYSTEM="APFS"
FORMAT="UDBZ"  # Compressed format

# Parse arguments
CLEAN_BUILD=0
SHOW_HELP=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN_BUILD=1
            shift
            ;;
        --help|-h)
            SHOW_HELP=1
            shift
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${NC}"
            SHOW_HELP=1
            shift
            ;;
    esac
done

# Show help
if [[ $SHOW_HELP -eq 1 ]]; then
    echo
    echo "PowerTrader AI - macOS DMG Installer Creation Script"
    echo
    echo "Usage: ./create_dmg_installer.sh [options]"
    echo
    echo "Options:"
    echo "  --clean    Remove existing DMG before creating new one"
    echo "  --help     Show this help message"
    echo
    exit 0
fi

# Helper functions
print_header() {
    echo
    echo -e "${BLUE}================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}================================================================${NC}"
    echo
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Print banner
print_header "PowerTrader AI - macOS DMG Installer Creation"

# Clean if requested
if [[ $CLEAN_BUILD -eq 1 ]]; then
    print_header "Cleaning Existing DMG"
    if [[ -f "$OUTPUT_DMG" ]]; then
        print_info "Removing existing DMG: $OUTPUT_DMG"
        rm -f "$OUTPUT_DMG"
        print_success "Existing DMG removed"
    else
        print_info "No existing DMG found"
    fi
fi

# Check requirements
print_header "Checking Requirements"

# Check create-dmg
if ! command -v create-dmg &> /dev/null; then
    print_error "create-dmg not found"
    print_info "Install with: brew install create-dmg"
    exit 1
fi
print_success "create-dmg found: $(command -v create-dmg)"

# Check if .app bundle exists
if [[ ! -d "$APP_BUNDLE" ]]; then
    print_error "Application bundle not found: $APP_BUNDLE"
    print_info "Run build_mac.sh first to build the application"
    exit 1
fi
print_success "Application bundle found: $APP_BUNDLE"

# Get .app size
APP_SIZE=$(du -sh "$APP_BUNDLE" | cut -f1)
print_info "Application size: $APP_SIZE"

# Check icon file
if [[ ! -f "$ICON_FILE" ]]; then
    print_warning "Icon file not found: $ICON_FILE"
    print_info "DMG will be created without custom icon"
    ICON_FILE=""
else
    print_success "Icon file found: $ICON_FILE"
fi

# Check if DMG already exists
if [[ -f "$OUTPUT_DMG" ]]; then
    print_warning "DMG already exists: $OUTPUT_DMG"
    print_info "Use --clean to remove it before building"
    exit 1
fi

# Create DMG
print_header "Creating DMG Installer"

print_info "Volume name: $VOLNAME"
print_info "Window size: $WINDOW_SIZE"
print_info "Icon size: $ICON_SIZE"
print_info "Filesystem: $FILESYSTEM"
print_info "Format: $FORMAT"
echo

# Build create-dmg command
CMD=(
    create-dmg
    --volname "$VOLNAME"
    --window-size $WINDOW_SIZE
    --icon-size $ICON_SIZE
    --icon "PowerTrader_AI.app" $APP_ICON_POS
    --app-drop-link $APPLICATIONS_LINK_POS
    --filesystem "$FILESYSTEM"
    --format "$FORMAT"
)

# Add icon if available
if [[ -n "$ICON_FILE" ]]; then
    CMD+=(--volicon "$ICON_FILE")
fi

# Add output file and source
CMD+=("$OUTPUT_DMG" "$APP_BUNDLE")

# Run create-dmg
print_info "Running: ${CMD[*]}"
echo

if "${CMD[@]}"; then
    echo
    print_success "DMG created successfully"
else
    print_error "Failed to create DMG"
    exit 1
fi

# Verify DMG
print_header "Verification"

if [[ ! -f "$OUTPUT_DMG" ]]; then
    print_error "DMG file not found after creation"
    exit 1
fi

DMG_SIZE=$(du -sh "$OUTPUT_DMG" | cut -f1)
print_success "DMG file exists: $OUTPUT_DMG"
print_success "DMG size: $DMG_SIZE"

# Calculate compression ratio
APP_SIZE_BYTES=$(du -s "$APP_BUNDLE" | cut -f1)
DMG_SIZE_BYTES=$(du -s "$OUTPUT_DMG" | cut -f1)
COMPRESSION_RATIO=$((100 - (DMG_SIZE_BYTES * 100 / APP_SIZE_BYTES)))
print_success "Compression: ${COMPRESSION_RATIO}% smaller than uncompressed app"

# Test mount
print_info "Testing DMG mount..."
if hdiutil attach "$OUTPUT_DMG" -readonly -nobrowse -mountpoint /tmp/powertrader_dmg_test &> /dev/null; then
    print_success "DMG mounted successfully"

    # Check contents
    if [[ -d "/tmp/powertrader_dmg_test/PowerTrader_AI.app" ]]; then
        print_success "Application bundle found in DMG"
    else
        print_error "Application bundle not found in DMG"
    fi

    if [[ -L "/tmp/powertrader_dmg_test/Applications" ]]; then
        print_success "Applications link found in DMG"
    else
        print_warning "Applications link not found in DMG"
    fi

    # Unmount
    hdiutil detach /tmp/powertrader_dmg_test &> /dev/null
    print_success "DMG unmounted successfully"
else
    print_error "Failed to mount DMG for testing"
    exit 1
fi

# Summary
print_header "Summary"

echo "DMG Installer Created:"
echo "  Location: $OUTPUT_DMG"
echo "  Size: $DMG_SIZE"
echo "  Compression: ${COMPRESSION_RATIO}%"
echo "  Format: $FORMAT (APFS)"
echo
echo "Installation Instructions:"
echo "  1. Double-click PowerTrader_AI.dmg"
echo "  2. Drag PowerTrader AI icon to Applications folder"
echo "  3. Eject the DMG"
echo "  4. Launch from Applications"
echo
print_success "DMG installer ready for distribution!"
