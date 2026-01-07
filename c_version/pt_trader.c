#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "common.h"
#include <sys/stat.h>
#include <unistd.h>

// Simplified trader: reads rh00d.sct (API credentials), reads neural outputs and writes hub data files.

double rand_price(double base) {
    double drift = ((rand() % 2001) - 1000) / 10000.0; // -0.1 .. +0.1
    return base * (1.0 + drift);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    srand((unsigned)time(NULL));

    // ensure hub_data dir (0700)
    struct stat st = {0};
    if (stat("hub_data", &st) == -1) {
        if (mkdir("hub_data", 0700) != 0) {
            fprintf(stderr, "Failed to create hub_data directory\n");
            return 1;
        }
    } else {
        // ensure restrictive perms
        chmod("hub_data", 0700);
    }

    // read credentials from rh00d.sct
    char *api_key = NULL;
    char *private_key = NULL;
    if (!read_rh00d_credentials("rh00d.sct", &api_key, &private_key)) {
        fprintf(stderr, "pt_trader.c: rh00d.sct missing or invalid; exiting.\n");
        return 1;
    }
    // Security: ensure rh00d.sct not group/world readable
    struct stat sk;
    if (stat("rh00d.sct", &sk) == 0) {
        if (sk.st_mode & (S_IRWXG | S_IRWXO)) {
            fprintf(stderr, "Security error: rh00d.sct has group/other permissions. Set to 0600 and try again.\n");
            if (api_key) free(api_key);
            if (private_key) free(private_key);
            return 1;
        }
    }
    if (api_key) free(api_key);
    if (private_key) free(private_key);

    // find coins from gui_settings.json
    char *cfg = read_text_file_alloc("gui_settings.json");
    char coins[256] = "BTC,ETH,XRP,BNB,DOGE";
    if (cfg) {
        char *p = strstr(cfg, "\"coins\"");
        if (p) {
            char *l = strchr(p, '[');
            char *r = l ? strchr(l, ']') : NULL;
            if (l && r && (r>l)) {
                size_t n = (size_t)(r-l-1);
                if (n < sizeof(coins)) {
                    strncpy(coins, l+1, n);
                    coins[n] = '\0';
                }
            }
        }
        free(cfg);
    }

    // Prepare a simple account snapshot and positions
    double buying_power = 1000.0 + (rand() % 10000) / 100.0;
    double holdings_sell_value = 0.0;

    // iterate coins and produce positions if files exist
    char *tok = strtok(coins, ",");
    while (tok) {
        while (*tok==' '||*tok=='\"') ++tok;
        char sym[16]; int i=0; while (tok[i] && tok[i]!=',' && i<15) { sym[i]=tok[i]; ++i; } sym[i]='\0';
        if (strlen(sym)==0) { tok = strtok(NULL, ","); continue; }

        char lowpath[256];
        snprintf(lowpath, sizeof(lowpath), "%s/low_bound_prices.html", (strcmp(sym,"BTC")==0)?".":sym);
        char longsig[256]; snprintf(longsig, sizeof(longsig), "%s/long_dca_signal.txt", (strcmp(sym,"BTC")==0)?".":sym);

        double simulated_price = 100.0 + (rand() % 50000) / 100.0;
        // if low file exists, read first number
        char *lp = read_text_file_alloc(lowpath);
        if (lp) {
            // parse first float
            double v = 0.0;
            sscanf(lp, "%lf", &v);
            if (v>0.0) simulated_price = v * (1.0 + ((rand()%100)/10000.0));
            free(lp);
        }

        int long_sig = 0;
        char *ls = read_text_file_alloc(longsig);
        if (ls) {
            long_sig = atoi(ls);
            free(ls);
        }

        // write per-coin current price file
        char curp[256]; snprintf(curp, sizeof(curp), "%s_current_price.txt", sym);
        char buf[64]; snprintf(buf, sizeof(buf), "%f\n", simulated_price);
        write_text_file(curp, buf);

        // if long_sig >= 3 and not held, simulate a buy by writing trade_history
        if (long_sig >= 3) {
            FILE *f = fopen("hub_data/trade_history.jsonl", "a");
            if (f) {
                long trade_ts = now_ts();
                fprintf(f, "{\"ts\": %ld, \"side\": \"buy\", \"symbol\": \"%s-USD\", \"qty\": %.6f, \"price\": %.6f, \"tag\": \"BUY\"}\n", trade_ts, sym, 0.001, simulated_price);
                fclose(f);
            }
            holdings_sell_value += simulated_price * 0.001;
        }

        tok = strtok(NULL, ",");
    }

    double total_account_value = buying_power + holdings_sell_value;

    // write trader_status.json (atomic-ish then set 0600)
    char status[1024];
    long ts = now_ts();
    snprintf(status, sizeof(status), "{\"timestamp\": %ld, \"account\": {\"total_account_value\": %.2f, \"buying_power\": %.2f, \"holdings_sell_value\": %.2f, \"percent_in_trade\": %.2f}, \"positions\": {}}\n", ts, total_account_value, buying_power, holdings_sell_value, (total_account_value>0)?(holdings_sell_value/total_account_value*100.0):0.0);
    write_text_file("hub_data/trader_status.json.tmp", status);
    rename("hub_data/trader_status.json.tmp", "hub_data/trader_status.json");
    chmod("hub_data/trader_status.json", S_IRUSR | S_IWUSR);

    // write pnl_ledger.json
    write_text_file("hub_data/pnl_ledger.json.tmp", "{\"total_realized_profit_usd\": 0.0}\n");
    rename("hub_data/pnl_ledger.json.tmp", "hub_data/pnl_ledger.json");
    chmod("hub_data/pnl_ledger.json", S_IRUSR | S_IWUSR);

    // append account value history (set perms if new)
    FILE *af = fopen("hub_data/account_value_history.jsonl", "a");
    if (af) {
        long history_ts = now_ts();
        fprintf(af, "{\"ts\": %ld, \"total_account_value\": %.2f}\n", history_ts, total_account_value);
        fclose(af);
        chmod("hub_data/account_value_history.jsonl", S_IRUSR | S_IWUSR);
    }

    // ensure trade_history has correct perms
    chmod("hub_data/trade_history.jsonl", S_IRUSR | S_IWUSR);

    // write runner_ready.json (atomic)
    char runner_ready[256];
    snprintf(runner_ready, sizeof(runner_ready), "{\"timestamp\": %ld, \"ready\": true, \"stage\": \"real_predictions\", \"ready_coins\": [], \"total_coins\": 0}\n", ts);
    write_text_file("hub_data/runner_ready.json.tmp", runner_ready);
    rename("hub_data/runner_ready.json.tmp", "hub_data/runner_ready.json");
    chmod("hub_data/runner_ready.json", S_IRUSR | S_IWUSR);

    printf("pt_trader.c: wrote simplified trader status to hub_data.\n");
    return 0;
}
