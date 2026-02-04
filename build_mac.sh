#!/bin/bash
###############################################################################
# PowerTrader AI - macOS Build Script
#
# This script builds all PowerTrader AI components for macOS using PyInstaller.
# It creates standalone .app bundles that can be distributed to users.
#
# Components built:
#   - PowerTrader_AI.app (Main GUI)
#   - pt_thinker (AI signal processor)
#   - pt_trader (Trading engine)
#
# Requirements:
#   - Python 3.12+
#   - PyInstaller 6.18.0+
#   - All dependencies from requirements.txt installed
#
# Usage:
#   ./build_mac.sh [options]
#
# Options:
#   --clean    Remove build and dist directories before building
#   --help     Show this help message
#
# Author: PowerTrader AI Contributors
# License: Apache 2.0
###############################################################################

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build"
DIST_DIR="${PROJECT_ROOT}/dist"

# Components to build
COMPONENTS=("pt_hub" "pt_thinker" "pt_trader")

###############################################################################
# Functions
###############################################################################

print_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "  $1"
}

show_help() {
    grep '^#' "$0" | grep -v '#!/bin/bash' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

clean_build() {
    print_header "Cleaning Build Directories"

    if [ -d "$BUILD_DIR" ]; then
        print_info "Removing ${BUILD_DIR}..."
        rm -rf "$BUILD_DIR"
        print_success "Build directory removed"
    else
        print_info "Build directory does not exist"
    fi

    if [ -d "$DIST_DIR" ]; then
        print_info "Removing ${DIST_DIR}..."
        rm -rf "$DIST_DIR"
        print_success "Dist directory removed"
    else
        print_info "Dist directory does not exist"
    fi

    echo ""
}

check_requirements() {
    print_header "Checking Requirements"

    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 not found. Please install Python 3.12 or higher."
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    print_success "Python ${PYTHON_VERSION} found"

    # Check PyInstaller
    if ! python3 -c "import PyInstaller" 2> /dev/null; then
        print_error "PyInstaller not found. Please install: pip install pyinstaller"
        exit 1
    fi

    PYINSTALLER_VERSION=$(python3 -c "import PyInstaller; print(PyInstaller.__version__)")
    print_success "PyInstaller ${PYINSTALLER_VERSION} found"

    # Check spec files
    for component in "${COMPONENTS[@]}"; do
        if [ ! -f "${PROJECT_ROOT}/${component}.spec" ]; then
            print_error "Spec file not found: ${component}.spec"
            exit 1
        fi
        print_success "Found ${component}.spec"
    done

    echo ""
}

build_component() {
    local component=$1
    local spec_file="${PROJECT_ROOT}/${component}.spec"

    print_header "Building ${component}"

    print_info "Building with PyInstaller..."
    if pyinstaller --clean --noconfirm "$spec_file"; then
        print_success "${component} built successfully"

        # Check output
        if [ "$component" = "pt_hub" ]; then
            # pt_hub creates .app bundle
            if [ -d "${DIST_DIR}/PowerTrader_AI.app" ]; then
                SIZE=$(du -sh "${DIST_DIR}/PowerTrader_AI.app" | awk '{print $1}')
                print_info "Output: PowerTrader_AI.app (${SIZE})"
            fi
        else
            # Other components create directory bundles
            if [ -d "${DIST_DIR}/${component}" ]; then
                SIZE=$(du -sh "${DIST_DIR}/${component}" | awk '{print $1}')
                print_info "Output: ${component}/ (${SIZE})"
            fi
        fi
    else
        print_error "Failed to build ${component}"
        return 1
    fi

    echo ""
}

build_all() {
    print_header "Building All Components"
    echo ""

    local failed=0

    for component in "${COMPONENTS[@]}"; do
        if ! build_component "$component"; then
            ((failed++))
        fi
    done

    return $failed
}

show_summary() {
    print_header "Build Summary"

    echo -e "Build directory: ${BUILD_DIR}"
    echo -e "Distribution directory: ${DIST_DIR}"
    echo ""

    print_info "Built components:"

    # Check pt_hub (.app bundle)
    if [ -d "${DIST_DIR}/PowerTrader_AI.app" ]; then
        SIZE=$(du -sh "${DIST_DIR}/PowerTrader_AI.app" | awk '{print $1}')
        print_success "PowerTrader_AI.app (${SIZE})"
    else
        print_error "PowerTrader_AI.app not found"
    fi

    # Check other components
    for component in "pt_thinker" "pt_trader"; do
        if [ -d "${DIST_DIR}/${component}" ]; then
            SIZE=$(du -sh "${DIST_DIR}/${component}" | awk '{print $1}')
            print_success "${component}/ (${SIZE})"
        else
            print_error "${component}/ not found"
        fi
    done

    echo ""
    print_info "Total distribution size: $(du -sh ${DIST_DIR} | awk '{print $1}')"
    echo ""
}

###############################################################################
# Main
###############################################################################

# Change to project root
cd "$PROJECT_ROOT"

# Parse arguments
CLEAN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Print banner
print_header "PowerTrader AI - macOS Build Script"
echo -e "  Platform: macOS $(sw_vers -productVersion)"
echo -e "  Architecture: $(uname -m)"
echo ""

# Clean if requested
if [ "$CLEAN" = true ]; then
    clean_build
fi

# Check requirements
check_requirements

# Build all components
if build_all; then
    show_summary
    print_success "Build completed successfully!"
    exit 0
else
    print_error "Build failed with errors"
    exit 1
fi
