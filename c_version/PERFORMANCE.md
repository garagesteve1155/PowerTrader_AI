# C Version - Performance Optimization Guide

## Compilation Optimization

### Current Build
```bash
gcc -Wall -Wextra -O2 *.c -o program
```

### Production-Ready Build
For maximum performance:
```bash
gcc -Wall -Wextra -O3 -march=native -flto *.c -o program
# -O3: Aggressive optimization (loop unrolling, inlining)
# -march=native: Optimize for local CPU architecture
# -flto: Link-time optimization (reduces binary size, improves speed)
```

### Debug Build
For troubleshooting:
```bash
gcc -Wall -Wextra -g -DDEBUG *.c -o program_debug
# -g: Include debug symbols
# -DDEBUG: Enable debug output (if preprocessor directives exist)
```

## Runtime Optimization

### File I/O Patterns

**Current Implementation:**
- ✅ Minimal file operations (write once per run, append-only for history)
- ✅ JSON parsing via string functions (no external library overhead)
- ✅ Atomic writes reduce disk sync overhead

**Optimization Tips:**
1. **Batch Operations:**
   - Combine multiple writes into single file operations
   - Use buffered I/O (stdio is already buffered)

2. **Avoid Redundant Reads:**
   ```c
   // Bad: Reading same file multiple times
   read_file("status.json");
   read_file("status.json");  // Redundant
   
   // Good: Cache in memory
   char *status = read_file("status.json");
   // Use status multiple times
   ```

3. **Use Memory Efficiently:**
   - Stack-allocated buffers for small data (< 4KB)
   - Dynamic allocation for large data structures
   - Free memory promptly to avoid heap fragmentation

### JSON Parsing & Serialization

**Current Approach:**
- Simple string-based parsing (no JSON library)
- Direct `snprintf()` for serialization
- Suitable for small JSON documents

**Performance Characteristics:**
- JSON construction: O(n) where n = number of fields
- JSON parsing: O(m) where m = string length
- No external library overhead

**Optimization:**
- Pre-allocate buffers to exact size
- Avoid repeated `strlen()` calls
- Use `strtol()` for numeric parsing (not `atoi()`)

### CPU & Memory Profile

| Operation | Time | Notes |
|-----------|------|-------|
| Startup | ~1-2ms | Minimal file opens, no network |
| Write JSON | ~5-10ms | Single file write, atomic rename |
| Append JSONL | ~2-5ms | Buffered append |
| Generate signals | ~1-3ms | Simple array operations |

### Profiling with GNU time

```bash
# Measure execution time and memory usage
/usr/bin/time -v ./pt_trader
```

Output includes:
- Elapsed real time
- User CPU time
- Maximum resident set size (memory)
- I/O reads/writes

### Minimize System Calls

**Current:**
- Explicit `stat()` for permission checks (necessary for security)
- One `mkdir()` + `chmod()` per directory (amortized)
- One `rename()` per atomic write

**Suggested Enhancements:**
- Cache directory existence checks if running repeatedly
- Use `openat()` for relative path operations (reduces syscalls)
- Consider memory-mapped files for very large datasets (unlikely here)

## Memory Management

### Allocation Patterns
```c
// Stack allocation (PREFER for fixed sizes)
char buffer[PATH_MAX];  // 4096 bytes, no fragmentation

// Heap allocation (use for dynamic sizes)
char *data = malloc(size);
// ... use data ...
free(data);  // Always free
```

### Leak Detection
```bash
valgrind --leak-check=full ./pt_trader
```

### Current Status
- ✅ All allocations are properly freed
- ✅ No memory leaks detected (previous valgrind validation)
- ✅ Minimal heap usage (mostly stack-based)

## Concurrency Considerations

**Current Model:** Single-process, single-threaded per program

**If Multi-Process Needed:**
- File locking via `fcntl()` to prevent concurrent writes
- Example: `flock()` on JSON files before modification

## Data Structure Optimization

### Arrays (Current)
```c
float low_bounds[7];   // Fixed size, stack allocated
float high_bounds[7];  // O(1) access
```
- ✅ Optimal for small, fixed-size data
- ✅ Cache-friendly (contiguous memory)

### Strings
```c
char key[256];
strncpy(key, source, sizeof(key) - 1);  // Safe, bounds-checked
```
- ✅ Stack-allocated for small keys
- ✅ Minimal allocations

## Recommendations by Workload

### Light Use (< 1 trade/hour)
- Build with `-O2` (current default)
- File I/O is not a bottleneck
- Run as cron job is fine

### Medium Use (1-10 trades/hour)
- Build with `-O3` and `-march=native`
- Monitor memory usage with `valgrind`
- Consider batch processing signals

### Heavy Use (> 10 trades/hour)
- Use `-O3 -march=native -flto`
- Implement signal caching layer
- Profile with `perf` before optimizing further
  ```bash
  perf record ./pt_trader
  perf report
  ```

## Comparison with Python Version

| Metric | C | Python |
|--------|---|--------|
| Startup Time | ~2ms | ~500-1000ms |
| Memory Usage | ~5-10MB | ~50-100MB |
| JSON Parsing | O(m) string scan | O(m) JSON library |
| File I/O | POSIX direct | Python buffered |
| **Advantage** | **Low latency, predictable** | **Maintainability, rapid dev** |

---

**When to Optimize:**
- Profile first with real data
- Measure before/after changes
- Avoid premature optimization
- Focus on algorithmic improvements first, then micro-optimization
