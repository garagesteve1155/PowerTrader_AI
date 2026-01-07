#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "common.h"

#include <sys/stat.h>

// Simplified port of pt_thinker.py: creates low/high bound files and signals randomly.

int rand_between(int a, int b) {
    return a + rand() % (b - a + 1);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    srand((unsigned)time(NULL));

    // Read gui_settings.json to find coins (best-effort)
    char *s = read_text_file_alloc("gui_settings.json");
    char coins[512] = "BTC,ETH,XRP,BNB,DOGE";
    if (s) {
        char *p = strstr(s, "\"coins\"");
        if (p) {
            // crude parse: find first [ and ] after coins and copy inside
            char *l = strchr(p, '[');
            char *r = l ? strchr(l, ']') : NULL;
            if (l && r && (r > l)) {
                size_t n = (size_t)(r - l - 1);
                if (n < sizeof(coins)) {
                    char tmp[512] = {0};
                    strncpy(tmp, l + 1, n);
                    // replace quotes and commas with commas
                    for (size_t i = 0; i < n; ++i) if (tmp[i] == '"' || tmp[i] == '\'') tmp[i] = ' ';
                    // collapse spaces to commas
                    for (size_t i = 0; i < n; ++i) if (tmp[i] == '\n' || tmp[i] == '\t') tmp[i] = ' ';
                    // simple: keep letters, commas
                    char out[512] = "";
                    size_t oi = 0;
                    for (size_t i = 0; i < n && oi + 1 < sizeof(out); ++i) {
                        char c = tmp[i];
                        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) { out[oi++] = c; }
                        else if (c == ',' || c == ' ' || c == '\\') { out[oi++] = ','; }
                    }
                    out[oi] = '\0';
                    if (oi > 0) strncpy(coins, out, sizeof(coins)-1);
                }
            }
        }
        free(s);
    }

    // tokenize coins by comma
    char *tok = strtok(coins, ",");
    while (tok) {
        // trim
        while (*tok == ' ') ++tok;
        char sym[16];
        size_t j = 0;
        for (; tok[j] && tok[j] != ',' && j < sizeof(sym)-1; ++j) sym[j] = tok[j];
        sym[j] = '\0';
        if (strlen(sym) == 0) { tok = strtok(NULL, ","); continue; }

        // ensure folder exists
            char folder[256];
            if (strcmp(sym, "BTC") == 0) strcpy(folder, "."); else snprintf(folder, sizeof(folder), "%s", sym);
            // ensure folder exists (0700)
            struct stat stf = {0};
            if (stat(folder, &stf) == -1) {
                if (mkdir(folder, 0700) != 0) {
                    fprintf(stderr, "Warning: could not create folder %s\n", folder);
                }
            } else {
                chmod(folder, 0700);
            }

        // write low_bound_prices.html and high_bound_prices.html with simple numeric lists
        char low_path[512];
        char high_path[512];
        snprintf(low_path, sizeof(low_path), "%s/low_bound_prices.html", folder);
        snprintf(high_path, sizeof(high_path), "%s/high_bound_prices.html", folder);

        // generate 7 descending high levels and 7 ascending low levels around a base price
        double base = 100.0 + rand_between(0, 50000) / 100.0; // random base
        double highs[7], lows[7];
        for (int i = 0; i < 7; ++i) {
            highs[i] = base * (1.0 + (0.01 * (7 - i)) );
            lows[i] = base * (1.0 - (0.01 * (i + 1)) );
        }
        FILE *fl = fopen(low_path, "w");
        if (fl) {
            for (int i = 0; i < 7; ++i) {
                fprintf(fl, "%.6f%s", lows[i], (i+1<7)?", ":"\n");
            }
            fclose(fl);
        }
        FILE *fh = fopen(high_path, "w");
        if (fh) {
            for (int i = 0; i < 7; ++i) {
                fprintf(fh, "%.6f%s", highs[i], (i+1<7)?", ":"\n");
            }
            fclose(fh);
        }

        // write long_dca_signal.txt and short_dca_signal.txt (0..7)
        char longp[512], shortp[512];
        snprintf(longp, sizeof(longp), "%s/long_dca_signal.txt", folder);
        snprintf(shortp, sizeof(shortp), "%s/short_dca_signal.txt", folder);
        int long_sig = rand_between(0, 7);
        int short_sig = rand_between(0, 3); // less shorts
        char buf[64];
        snprintf(buf, sizeof(buf), "%d\n", long_sig);
        write_text_file(longp, buf);
        snprintf(buf, sizeof(buf), "%d\n", short_sig);
        write_text_file(shortp, buf);

        tok = strtok(NULL, ",");
    }

    // write runner_ready.json
    char ready[512];
    snprintf(ready, sizeof(ready), "{\"timestamp\": %ld, \"ready\": true, \"stage\": \"real_predictions\", \"ready_coins\": [], \"total_coins\": 0}\n", now_ts());
    write_text_file("hub_data/runner_ready.json", ready);

    printf("pt_thinker.c: generated simple neural outputs (low/high files and signals).\n");
    return 0;
}
