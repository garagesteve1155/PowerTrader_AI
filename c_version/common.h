#ifndef COMMON_H
#define COMMON_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static inline long now_ts() {
    return (long)time(NULL);
}

static inline int write_text_file(const char *path, const char *txt) {
    FILE *f = fopen(path, "w");
    if (!f) return 0;
    fputs(txt, f);
    fclose(f);
    return 1;
}

static inline char *read_text_file_alloc(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1);
    if (!buf) { fclose(f); return NULL; }
    fread(buf, 1, sz, f);
    buf[sz] = '\0';
    fclose(f);
    return buf;
}

// Parse rh00d.sct JSON file and extract api_key and private_key
// Returns 1 on success, 0 on failure
// Caller must free api_key and private_key if successful
static inline int read_rh00d_credentials(const char *path, char **api_key, char **private_key) {
    if (!api_key || !private_key) return 0;
    *api_key = NULL;
    *private_key = NULL;
    
    char *content = read_text_file_alloc(path);
    if (!content) return 0;
    
    // Simple JSON parsing for api_key and private_key fields
    char *api_start = strstr(content, "\"api_key\"");
    char *priv_start = strstr(content, "\"private_key\"");
    
    if (!api_start || !priv_start) {
        free(content);
        return 0;
    }
    
    // Extract api_key value
    char *api_quote_start = strchr(api_start, ':');
    if (api_quote_start) {
        api_quote_start = strchr(api_quote_start, '"');
        if (api_quote_start) {
            char *api_quote_end = strchr(api_quote_start + 1, '"');
            if (api_quote_end) {
                int len = (int)(api_quote_end - api_quote_start - 1);
                if (len > 0 && len < 512) {
                    *api_key = malloc(len + 1);
                    if (*api_key) {
                        strncpy(*api_key, api_quote_start + 1, len);
                        (*api_key)[len] = '\0';
                    }
                }
            }
        }
    }
    
    // Extract private_key value
    char *priv_quote_start = strchr(priv_start, ':');
    if (priv_quote_start) {
        priv_quote_start = strchr(priv_quote_start, '"');
        if (priv_quote_start) {
            char *priv_quote_end = strchr(priv_quote_start + 1, '"');
            if (priv_quote_end) {
                int len = (int)(priv_quote_end - priv_quote_start - 1);
                if (len > 0 && len < 512) {
                    *private_key = malloc(len + 1);
                    if (*private_key) {
                        strncpy(*private_key, priv_quote_start + 1, len);
                        (*private_key)[len] = '\0';
                    }
                }
            }
        }
    }
    
    free(content);
    
    // Success if both were extracted
    if (*api_key && *private_key) {
        return 1;
    }
    
    // Cleanup on partial failure
    if (*api_key) { free(*api_key); *api_key = NULL; }
    if (*private_key) { free(*private_key); *private_key = NULL; }
    return 0;
}

#endif
