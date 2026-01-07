#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common.h"

// Minimal console replacement for the GUI hub: shows hub_data status and coin signals

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("PowerTrader AI - Console Hub (simplified)\n");

    // show runner_ready
    char *r = read_text_file_alloc("hub_data/runner_ready.json");
    if (r) {
        printf("runner_ready.json:\n%s\n", r);
        free(r);
    } else {
        printf("runner_ready.json: (not present)\n");
    }

    // show trader_status
    char *t = read_text_file_alloc("hub_data/trader_status.json");
    if (t) {
        printf("trader_status.json:\n%s\n", t);
        free(t);
    } else {
        printf("trader_status.json: (not present)\n");
    }

    // list coin folders with long/short signals
    const char *coins[] = {"BTC","ETH","XRP","BNB","DOGE",NULL};
    for (int i = 0; coins[i]; ++i) {
        char longp[256], shortp[256];
        snprintf(longp, sizeof(longp), "%s/long_dca_signal.txt", (strcmp(coins[i],"BTC")==0)?".":coins[i]);
        snprintf(shortp, sizeof(shortp), "%s/short_dca_signal.txt", (strcmp(coins[i],"BTC")==0)?".":coins[i]);
        char *L = read_text_file_alloc(longp);
        char *S = read_text_file_alloc(shortp);
        printf("%s: long=%s short=%s\n", coins[i], L?L:"0", S?S:"0");
        if (L) free(L);
        if (S) free(S);
    }

    return 0;
}
