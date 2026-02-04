# Assets Requirements - PowerTrader AI

This document specifies all required assets for PowerTrader AI executables and documentation.

## Application Icons

### macOS Application Icon (.icns)

**Filename**: `icon.icns`
**Location**: `assets/icon.icns`
**Format**: Apple Icon Image (.icns)
**File Size**: 200-500 KB

**Required Resolutions**:
- 16x16 (Finder list view)
- 32x32 (Finder list view @2x)
- 64x64 (Finder icon view)
- 128x128 (Finder icon view @2x)
- 256x256 (Finder icon view, Dock)
- 512x512 (Finder icon view @2x, Dock @2x)
- 1024x1024 (Retina displays)

**Creation Command**:
```bash
iconutil -c icns icon.iconset -o assets/icon.icns
```

---

### Windows Application Icon (.ico)

**Filename**: `icon.ico`
**Location**: `assets/icon.ico`
**Format**: Windows Icon (.ico)
**File Size**: 100-300 KB

**Required Resolutions**:
- 16x16 (Small taskbar)
- 24x24 (Small toolbar)
- 32x32 (Standard icons)
- 48x48 (Large icons)
- 64x64 (Extra large)
- 128x128 (Jumbo icons)
- 256x256 (High DPI)

---

## Design Guidelines

**Theme**: Technology, Trading, AI

**Suggested Elements**:
- Neural network visualization
- Trading chart elements
- Power/Energy symbol
- Modern, minimalist design

**Color Scheme** (Dracula theme):
- Primary: Purple (#BD93F9) or Pink (#FF79C6)
- Secondary: Cyan (#8BE9FD) or Green (#50FA7B)
- Background: Dark (#282A36)

---

## Documentation Assets (Optional)

### Logo Variants

1. **logo-horizontal.png** - 800x200 px (~50 KB)
2. **logo-mark.png** - 512x512 px (~30 KB)
3. **logo-inverse.png** - 800x200 px (~50 KB)

---

## Summary

**Required Assets**:
- icon.icns (200-500 KB) - macOS
- icon.ico (100-300 KB) - Windows

**Total Size**: ~300-800 KB

**Current Status**: Icons set to None (builds work without them)
**Priority**: Medium (cosmetic, not functional)
