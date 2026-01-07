# C Version - Security Best Practices (rh00d.sct Format)

## Overview
This C implementation prioritizes security for API key handling, file permissions, and system interactions using a unified `rh00d.sct` credentials file.

## API Key Security

### File Permissions
- **`rh00d.sct`**: Must have permissions `0600` (read/write owner only)
  - Program checks for group/world read permissions and fails if exposed
  - Single JSON file containing both API key and private key
  - **Never commit to version control**
  - Use `.gitignore` to prevent accidental commits

- **`hub_data/` Directory**: Created with `0700` permissions (owner only)
  - All JSON outputs in `hub_data/` have `0600` permissions
  - Ensures sensitive trading data cannot be read by other users

### rh00d.sct Format
```json
{
  "api_key": "rh.your_api_key_here",
  "private_key": "base64_encoded_ed25519_seed"
}
```

### File Validation
- API credentials file parsed from JSON
- Permissions explicitly validated (rejects group/world-readable files)
- Fails fast with clear error messages

### Secure File Writing
- Uses atomic write pattern: **write temp file → rename → chmod**
- Prevents partial/corrupted data and race conditions
- Applied to:
  - `hub_data/trader_status.json`
  - `hub_data/pnl_ledger.json`
  - `hub_data/account_value_history.jsonl`
  - `hub_data/trade_history.jsonl`
  - `hub_data/runner_ready.json`

## System Call Safety

### Avoided Shell Injection Risks
- ✅ Uses `mkdir()` + `chmod()` instead of `system("mkdir -p ...")`
- ✅ No `system()` calls for file operations
- ✅ Direct POSIX API calls only

### Buffer Management
- All string operations use bounds-checked variants:
  - `snprintf()` instead of `sprintf()`
  - `strncpy()` with length limits
  - Safe JSON field extraction in common.h

## Compilation & Hardening

### Build Security Flags
Recommended for production:
```bash
gcc -Wall -Wextra -O3 -march=native -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
    -fPIE -pie *.c -o pt_trader
```

### Current Build
The `Makefile` compiles with `-O2` and `-Wall -Wextra` for optimization and warnings.

## Runtime Best Practices

1. **Before Running:**
   - Ensure `r_secret.txt` exists and has `0600` permissions:
     ```bash
     chmod 600 r_secret.txt
     ```
   - Set restrictive umask (optional):
     ```bash
     umask 0077
     ```

2. **After Running:**
   - Verify `hub_data/` permissions:
     ```bash
     ls -ld hub_data/
     # Should show: d--------- (0700)
     ```
   - Check JSON file permissions:
     ```bash
     ls -l hub_data/*.json
     # Should show: -rw------- (0600)
     ```

3. **Monitoring:**
   - Review `account_value_history.jsonl` for trading activity
   - Audit `trade_history.jsonl` for transactions
   - Check `trainer_last_training_time.txt` timestamps

## Known Limitations & Mitigations

| Issue | Mitigation |
|-------|-----------|
| No encryption at rest | Use full-disk encryption or encrypted filesystem |
| No TLS for local files | Keep on trusted, local machines only |
| No authentication | Assume single-user, local system |
| Simplified trading logic | Use only with paper trading initially |

## Environment Assumptions

- **OS**: Linux/POSIX (uses `mkdir`, `chmod`, `rename`)
- **File System**: Supports POSIX permissions (ext4, XFS, etc.)
- **User**: Runs with own UID; recommend dedicated user account
- **Network**: Simplified/stubbed (no actual API calls in current build)

## Incident Response

**If `r_secret.txt` is compromised:**
1. Stop all running programs immediately
2. Revoke API keys from exchange
3. Generate new `r_secret.txt` with fresh credentials
4. Change file permissions to `0600`
5. Restart programs

**If `hub_data/` is leaked:**
- Contains trading data and PnL history; treat as sensitive
- Regenerate all trading history from exchange audit logs
- Reset `account_value_history.jsonl`

## Code Review Checklist

- [x] No hardcoded secrets or credentials
- [x] No shell invocations for file I/O
- [x] All file operations use atomic writes where applicable
- [x] API key files require explicit `0600` check before use
- [x] Directory creation enforces `0700` permissions
- [x] Buffer overflows prevented with bounds-checked functions
- [x] Error handling for all file operations
- [x] Consistent use of POSIX standard library functions
